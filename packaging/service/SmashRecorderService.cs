// Smash Replay Recorder Bluetooth service.
//
// Runs as LocalSystem so the recorder never needs its own permission prompt.
// It does exactly two privileged things, only for USB *Bluetooth* adapters:
//   BIND <busid>     hand the adapter to the recorder (usbipd bind --force)
//   RELEASE <busid>  give it back to Windows (usbipd detach + unbind)
// plus PING. Whatever a client bound is released when that client disconnects
// (app closed or crashed), and anything left bound is released when the
// service starts (e.g. after a power cut).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

public class SmashRecorderService : ServiceBase
{
    public const string PipeName = "SmashReplayRecorder";
    static readonly string StateDir = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "SmashReplayRecorder");
    static readonly string StateFile = Path.Combine(StateDir, "bound-adapters.txt");
    static readonly object Gate = new object();
    volatile bool stopping;

    public SmashRecorderService() { ServiceName = "SmashRecorderBluetooth"; CanStop = true; }

    static string Usbipd()
    {
        string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"usbipd-win\usbipd.exe");
        if (!File.Exists(path)) throw new InvalidOperationException("usbipd is not installed");
        return path;
    }

    static int Run(params string[] args)
    {
        var info = new ProcessStartInfo(Usbipd(), string.Join(" ", args))
        { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        using (var process = Process.Start(info))
        {
            process.StandardOutput.ReadToEnd(); process.StandardError.ReadToEnd();
            process.WaitForExit(60000);
            return process.ExitCode;
        }
    }

    static string Capture(params string[] args)
    {
        var info = new ProcessStartInfo(Usbipd(), string.Join(" ", args))
        { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, StandardOutputEncoding = Encoding.UTF8 };
        using (var process = Process.Start(info))
        {
            string output = process.StandardOutput.ReadToEnd();
            process.WaitForExit(60000);
            return output;
        }
    }

    // Returns (isBluetooth, isForced) for a bus ID from `usbipd state`.
    static bool[] Describe(string busId)
    {
        var state = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(Capture("state"));
        foreach (object item in (System.Collections.ArrayList)state["Devices"])
        {
            var device = (Dictionary<string, object>)item;
            if ((device["BusId"] as string) == busId)
            {
                string description = (device["Description"] as string) ?? "";
                bool forced = device.ContainsKey("IsForced") && device["IsForced"] is bool && (bool)device["IsForced"];
                return new[] { description.IndexOf("bluetooth", StringComparison.OrdinalIgnoreCase) >= 0, forced };
            }
        }
        return new[] { false, false };
    }

    static List<string> LoadState()
    {
        lock (Gate) { return File.Exists(StateFile) ? new List<string>(File.ReadAllLines(StateFile)) : new List<string>(); }
    }

    static void SaveState(List<string> ids)
    {
        lock (Gate) { Directory.CreateDirectory(StateDir); File.WriteAllLines(StateFile, ids.ToArray()); }
    }

    static void Remember(string busId, bool add)
    {
        lock (Gate)
        {
            var ids = LoadState();
            ids.Remove(busId);
            if (add) ids.Add(busId);
            SaveState(ids);
        }
    }

    static void Bind(string busId)
    {
        bool[] about = Describe(busId);
        if (!about[0]) throw new InvalidOperationException("only Bluetooth adapters can be shared");
        Remember(busId, true);   // before binding, so a crash still leads to release
        Run("bind", "--force", "--busid", busId);
        if (!Describe(busId)[1]) throw new InvalidOperationException("Windows would not release the adapter");
    }

    static void Release(string busId)
    {
        Run("detach", "--busid", busId);
        Run("unbind", "--busid", busId);
        Remember(busId, false);
    }

    void Serve(NamedPipeServerStream pipe)
    {
        var mine = new HashSet<string>();
        try
        {
            var reader = new StreamReader(pipe, new UTF8Encoding(false));
            var writer = new StreamWriter(pipe, new UTF8Encoding(false)) { AutoFlush = true };
            string line;
            while ((line = reader.ReadLine()) != null)
            {
                var match = Regex.Match(line.Trim(), @"^(PING|BIND|RELEASE)(?: ([0-9]{1,3}-[0-9]{1,3}))?$");
                if (!match.Success) { writer.WriteLine("ERR unknown request"); continue; }
                string command = match.Groups[1].Value, busId = match.Groups[2].Value;
                try
                {
                    if (command == "PING") { writer.WriteLine("OK"); continue; }
                    if (busId == "") throw new InvalidOperationException("missing adapter");
                    if (command == "BIND") { Bind(busId); mine.Add(busId); }
                    else { Release(busId); mine.Remove(busId); }
                    writer.WriteLine("OK");
                }
                catch (Exception error) { writer.WriteLine("ERR " + error.Message.Replace('\n', ' ')); }
            }
        }
        catch (IOException) { }
        finally
        {
            foreach (string busId in mine) { try { Release(busId); } catch { } }
            pipe.Dispose();
        }
    }

    void Listen()
    {
        var security = new PipeSecurity();
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.AuthenticatedUserSid, null),
                                                  PipeAccessRights.ReadWrite, AccessControlType.Allow));
        security.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                                                  PipeAccessRights.FullControl, AccessControlType.Allow));
        while (!stopping)
        {
            var pipe = new NamedPipeServerStream(PipeName, PipeDirection.InOut, 4, PipeTransmissionMode.Byte,
                                                 PipeOptions.None, 4096, 4096, security);
            try { pipe.WaitForConnection(); }
            catch { pipe.Dispose(); continue; }
            if (stopping) { pipe.Dispose(); break; }
            new Thread(() => Serve(pipe)) { IsBackground = true }.Start();
        }
    }

    protected override void OnStart(string[] args)
    {
        // Anything still handed over from before (crash, power cut) goes back to Windows.
        foreach (string busId in LoadState()) { try { Release(busId); } catch { } }
        new Thread(Listen) { IsBackground = true }.Start();
    }

    protected override void OnStop()
    {
        stopping = true;
        foreach (string busId in LoadState()) { try { Release(busId); } catch { } }
        // Unblock WaitForConnection.
        try { using (var client = new NamedPipeClientStream(".", PipeName)) client.Connect(500); } catch { }
    }

    public static void Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--console")   // developer testing
        {
            var service = new SmashRecorderService();
            service.OnStart(args);
            Console.WriteLine("Listening on \\\\.\\pipe\\" + PipeName + " — press Enter to stop");
            Console.ReadLine();
            service.OnStop();
            return;
        }
        ServiceBase.Run(new SmashRecorderService());
    }
}
