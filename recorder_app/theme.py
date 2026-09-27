"""StartSeeder-style look for Tk: tonal zinc surfaces, rounded cards, pill buttons.

Mirrors the StartSeeder web client's rules: system font, borderless tonal
cards, inverted-contrast primary button, tonal secondary buttons and
segmented controls, blue accents, green reserved for the logo and positive
data. The palette follows Windows' light/dark app setting.
"""
import ctypes
import tkinter as tk

FONT = 'Segoe UI'
LOGO_GREEN, LOGO_DARK_GREEN = '#7ab245', '#4b7d56'

ZINC = {50: '#f6f6f4', 100: '#ececea', 200: '#dcdcd9', 300: '#c6c8cb', 400: '#8e9298', 500: '#74777d',
        600: '#5b5f66', 700: '#262a2f', 800: '#1c1f23', 900: '#16181b', 950: '#101113'}

LIGHT = dict(page=ZINC[50], nav='#ffffff', nav_line=ZINC[100], card='#ffffff',
             card_line=ZINC[100], ink=ZINC[900], muted=ZINC[500], faint=ZINC[400],
             well=ZINC[100], well_hover=ZINC[200], segment_on='#ffffff',
             primary=ZINC[900], primary_ink='#ffffff', primary_hover=ZINC[700],
             accent='#2563eb', track=ZINC[100], good='#16a34a', warn='#dc2626', dark=False)
DARK = dict(page=ZINC[950], nav=ZINC[950], nav_line=ZINC[800], card=ZINC[900], card_line=ZINC[800],
            ink=ZINC[100], muted=ZINC[400], faint=ZINC[500], well=ZINC[800], well_hover=ZINC[700],
            segment_on=ZINC[700], primary='#ffffff', primary_ink=ZINC[900], primary_hover=ZINC[200],
            accent='#2563eb', track=ZINC[800], good='#22c55e', warn='#f87171', dark=True)


def windows_prefers_dark():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
    except OSError:
        return False


def palette():
    return DARK if windows_prefers_dark() else LIGHT


def match_title_bar(window, dark):
    """Dark or light window frame to match the content (Windows 10 20H1+)."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def rounded_rect(canvas, x1, y1, x2, y2, r, **options):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
              x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **options)


class Card(tk.Frame):
    """Rounded tonal surface; put widgets in `.inner` (a Frame with the card color).

    The frame sizes itself from its content like any other; a canvas placed
    behind the content draws the rounded background. The padding is larger
    than the corner radius, so the square inner frame never shows a corner.
    """

    def __init__(self, parent, colors, scale, padding=22, radius=12):
        super().__init__(parent, bg=parent['bg'])
        self.colors, self.radius = colors, int(radius * scale)
        self.surface = tk.Canvas(self, bg=parent['bg'], highlightthickness=0, bd=0)
        self.surface.place(x=0, y=0, relwidth=1, relheight=1)
        self.inner = tk.Frame(self, bg=colors['card'])
        pad = int(padding * scale)
        self.inner.pack(fill='both', expand=True, padx=pad, pady=pad)
        # Canvas.lower() means "lower a drawing item"; stack the widget itself below the content.
        self.tk.call('lower', self.surface._w, self.inner._w)
        self.surface.bind('<Configure>', self._redraw)

    def _redraw(self, _event=None):
        self.surface.delete('all')
        w, h = self.surface.winfo_width(), self.surface.winfo_height()
        rounded_rect(self.surface, 1, 1, w - 1, h - 1, self.radius, fill=self.colors['card'],
                     outline=self.colors['card_line'])


class PillButton(tk.Canvas):
    """Rounded button. kind: 'primary' (inverted contrast) or 'secondary' (tonal well)."""

    def __init__(self, parent, text, command, colors, scale, kind='secondary', size=11):
        super().__init__(parent, bg=parent['bg'], highlightthickness=0, bd=0, cursor='hand2')
        self.colors, self.command, self.kind, self.scale = colors, command, kind, scale
        self.font = (FONT, size, 'bold' if kind == 'primary' else 'normal')
        self.text, self.state, self.hover = text, 'normal', False
        self.bind('<Enter>', lambda e: self._set_hover(True))
        self.bind('<Leave>', lambda e: self._set_hover(False))
        self.bind('<ButtonRelease-1>', self._click)
        self._draw()

    def _set_hover(self, value):
        self.hover = value
        self._draw()

    def _click(self, _event):
        if self.state == 'normal' and self.command:
            self.command()

    def _draw(self):
        self.delete('all')
        c = self.colors
        if self.kind == 'primary':
            fill, ink = (c['primary_hover'] if self.hover else c['primary']), c['primary_ink']
        else:
            fill, ink = (c['well_hover'] if self.hover else c['well']), c['ink']
        if self.state == 'disabled':
            fill, ink = c['well'], c['faint']
        probe = self.create_text(0, 0, text=self.text, font=self.font, anchor='nw')
        x1, y1, x2, y2 = self.bbox(probe)
        self.delete(probe)
        padx, pady = int(16 * self.scale), int(8 * self.scale)
        width, height = (x2 - x1) + 2 * padx, (y2 - y1) + 2 * pady
        self.configure(width=width, height=height)
        rounded_rect(self, 0, 0, width, height, int(8 * self.scale), fill=fill, outline=fill)
        self.create_text(width / 2, height / 2, text=self.text, font=self.font, fill=ink)
        self.configure(cursor='hand2' if self.state == 'normal' else 'arrow')

    def configure(self, cnf=None, **options):
        changed = False
        for key in ('state', 'text'):
            if key in options:
                setattr(self, key, options.pop(key))
                changed = True
        result = super().configure(cnf, **options) if (cnf or options) else None
        if changed:
            self._draw()
        return result

    config = configure


class Segmented(tk.Frame):
    """StartSeeder segmented control: a tonal well whose selected segment is raised."""

    def __init__(self, parent, options, variable, command, colors, scale):
        super().__init__(parent, bg=colors['well'], padx=int(4 * scale), pady=int(4 * scale))
        self.colors, self.variable, self.command, self.buttons = colors, variable, command, {}
        for label in options:
            button = tk.Label(self, text=label, font=(FONT, 10, 'bold'), padx=int(14 * scale), pady=int(6 * scale),
                              cursor='hand2')
            button.pack(side='left', padx=(0, int(2 * scale)))
            button.bind('<Button-1>', lambda e, value=label: self.select(value))
            self.buttons[label] = button
        self.refresh()

    def select(self, value):
        self.variable.set(value)
        self.refresh()
        if self.command:
            self.command()

    def refresh(self):
        for label, button in self.buttons.items():
            on = label == self.variable.get()
            button.configure(bg=self.colors['segment_on'] if on else self.colors['well'],
                             fg=self.colors['ink'] if on else self.colors['muted'])


class Progress(tk.Canvas):
    """Thin rounded bar (StartSeeder ProgressBar)."""

    def __init__(self, parent, colors, scale, height=8):
        super().__init__(parent, bg=parent['bg'], highlightthickness=0, bd=0, height=int(height * scale))
        self.colors, self.value, self.maximum = colors, 0, 1
        self.bind('<Configure>', lambda e: self._draw())

    def set(self, value, maximum):
        self.value, self.maximum = value, max(1, maximum)
        self._draw()

    def _draw(self):
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return
        rounded_rect(self, 0, 0, w, h, h / 2, fill=self.colors['track'], outline=self.colors['track'])
        filled = int(w * min(1, self.value / self.maximum))
        if filled > h:
            rounded_rect(self, 0, 0, filled, h, h / 2, fill=self.colors['accent'], outline=self.colors['accent'])


def logo(parent, colors, scale, size=28):
    """The recorder's mark, drawn in StartSeeder's logo greens: a replay arrow around a play triangle."""
    s = int(size * scale)
    canvas = tk.Canvas(parent, width=s, height=s, bg=parent['bg'], highlightthickness=0, bd=0)
    w = max(2, int(2.4 * scale))
    canvas.create_arc(w, w, s - w, s - w, start=110, extent=300, style='arc', outline=LOGO_GREEN, width=w)
    tip_x, tip_y = s * .30, s * .12
    canvas.create_line(tip_x - s * .14, tip_y - s * .02, tip_x, tip_y + s * .02, tip_x - s * .08, tip_y + s * .15,
                       fill=LOGO_GREEN, width=w, capstyle='round', joinstyle='round')
    canvas.create_polygon(s * .42, s * .33, s * .42, s * .67, s * .70, s * .50, fill=LOGO_DARK_GREEN if not colors['dark']
                          else LOGO_GREEN, outline='')
    return canvas
