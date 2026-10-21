import math
import os
import struct
import subprocess
import zlib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
ICO_PATH = ASSETS_DIR / "icon.ico"


def _dist_to_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx = x2 - x1
    dy = y2 - y1
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-9:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / len_sq))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _dist_to_rotated_ellipse(
    px: float, py: float, cx: float, cy: float, rx: float, ry: float, angle_rad: float
) -> float:
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    lx = (px - cx) * cos_a + (py - cy) * sin_a
    ly = -(px - cx) * sin_a + (py - cy) * cos_a
    norm_d = math.hypot(lx / rx, ly / ry)
    return (norm_d - 1.0) * min(rx, ry)


def make_png(size: int) -> bytes:
    """
    Generates an RGBA PNG image of size x size with an Apple macOS squircle background
    and a minimalist Willow (Verba) branch with three fluffy silver-white catkins.
    Uses pure Python (zlib + struct) with zero external dependencies.
    """
    raw_rows = bytearray()
    cx = (size - 1) / 2.0
    cy = (size - 1) / 2.0
    half = size * 0.44
    corner_r = size * 0.22

    # Willow branch polyline segments in normalized coordinates (-1..1)
    stem_pts = [
        (-0.48, 0.58),
        (-0.22, 0.24),
        (0.04, -0.08),
        (0.32, -0.44),
    ]
    stem_radius = 0.036

    # Fluffy willow catkins (buds): (cx, cy, rx, ry, angle_deg)
    buds = [
        (-0.30, 0.08, 0.125, 0.185, -28.0),
        (0.16, 0.06, 0.125, 0.185, 32.0),
        (-0.04, -0.34, 0.125, 0.190, -22.0),
        (0.36, -0.48, 0.110, 0.165, 25.0),
    ]

    # Small warm wood cups at base of each willow bud: (cx, cy, r)
    cups = [
        (-0.24, 0.21, 0.065),
        (0.06, 0.17, 0.065),
        (0.02, -0.20, 0.065),
        (0.30, -0.38, 0.060),
    ]

    for y in range(size):
        raw_rows.append(0)  # PNG filter type 0 (None)
        ny = (y - cy) / half
        t_grad = y / max(1, size - 1)

        # Deep botanical slate-teal gradient: top (#16334B) -> bottom (#0E1E2E)
        bg_r = int(22 * (1 - t_grad) + 14 * t_grad)
        bg_g = int(51 * (1 - t_grad) + 30 * t_grad)
        bg_b = int(75 * (1 - t_grad) + 46 * t_grad)

        for x in range(size):
            nx = (x - cx) / half

            # Rounded rectangle SDF
            dx = max(abs(x - cx) - (half - corner_r), 0.0)
            dy = max(abs(y - cy) - (half - corner_r), 0.0)
            dist = math.hypot(dx, dy) - corner_r

            if dist > 0.8:
                raw_rows.extend((0, 0, 0, 0))
                continue

            bg_alpha = 1.0 if dist < -0.8 else max(0.0, min(1.0, 0.5 - dist / 1.6))

            # Subtle inner border highlight
            if dist > -size * 0.03 and y < cy:
                r_px = min(255, bg_r + 24)
                g_px = min(255, bg_g + 28)
                b_px = min(255, bg_b + 32)
            else:
                r_px, g_px, b_px = bg_r, bg_g, bg_b

            # 1. Draw willow branch stem (warm emerald-sage #4ADE80)
            stem_d = 999.0
            for (x1, y1), (x2, y2) in zip(stem_pts[:-1], stem_pts[1:]):
                d_seg = (_dist_to_segment(nx, ny, x1, y1, x2, y2) - stem_radius) * half
                if d_seg < stem_d:
                    stem_d = d_seg

            if stem_d < 0.8:
                sa = 1.0 if stem_d < -0.8 else max(0.0, min(1.0, 0.5 - stem_d / 1.6))
                r_px = int(r_px * (1 - sa) + 82 * sa)
                g_px = int(g_px * (1 - sa) + 214 * sa)
                b_px = int(b_px * (1 - sa) + 148 * sa)

            # 2. Draw fluffy silver-white willow catkins (buds)
            bud_d = 999.0
            for bx, by, brx, bry, bang in buds:
                d_b = _dist_to_rotated_ellipse(nx, ny, bx, by, brx, bry, math.radians(bang)) * half
                if d_b < bud_d:
                    bud_d = d_b

            if bud_d < 0.8:
                ba = 1.0 if bud_d < -0.8 else max(0.0, min(1.0, 0.5 - bud_d / 1.6))
                r_px = int(r_px * (1 - ba) + 248 * ba)
                g_px = int(g_px * (1 - ba) + 250 * ba)
                b_px = int(b_px * (1 - ba) + 252 * ba)

            # 3. Draw subtle golden-emerald bud base cups
            cup_d = 999.0
            for cx_cup, cy_cup, cr in cups:
                d_c = (math.hypot(nx - cx_cup, ny - cy_cup) - cr) * half
                if d_c < cup_d:
                    cup_d = d_c

            if cup_d < 0.8 and stem_d < 3.5:
                ca = 1.0 if cup_d < -0.8 else max(0.0, min(1.0, 0.5 - cup_d / 1.6))
                ca *= 0.85
                r_px = int(r_px * (1 - ca) + 52 * ca)
                g_px = int(g_px * (1 - ca) + 199 * ca)
                b_px = int(b_px * (1 - ca) + 128 * ca)

            a = int(bg_alpha * 255)
            raw_rows.extend((r_px, g_px, b_px, a))

    def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", idat)
        + png_chunk(b"IEND", b"")
    )


def build_ico(ico_path: Path) -> None:
    sizes = [256, 64, 48, 32, 16]
    png_blobs = [make_png(s) for s in sizes]

    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = bytearray()
    for s, blob in zip(sizes, png_blobs):
        w = 0 if s == 256 else s
        h = 0 if s == 256 else s
        entries.extend(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)

    with open(ico_path, "wb") as f:
        f.write(header)
        f.write(entries)
        for blob in png_blobs:
            f.write(blob)


CS_SOURCE = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

class VerbaScribeLauncher
{
    [DllImport("shell32.dll", SetLastError = true)]
    static extern void SetCurrentProcessExplicitAppUserModelID([MarshalAs(UnmanagedType.LPWStr)] string AppID);

    [STAThread]
    static void Main(string[] args)
    {
        try
        {
            SetCurrentProcessExplicitAppUserModelID("VerbaScribe.Desktop.Studio.1");
        }
        catch { }

        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        Directory.SetCurrentDirectory(baseDir);

        string venvPython = Path.Combine(baseDir, ".venv", "Scripts", "python.exe");
        string runPy = Path.Combine(baseDir, "run.py");
        string setupBat = Path.Combine(baseDir, "setup.bat");

        // First run on a new PC where .venv is not yet created:
        if (!File.Exists(venvPython))
        {
            if (File.Exists(setupBat))
            {
                DialogResult res = MessageBox.Show(
                    "Добро пожаловать в VerbaScribe!\n\n" +
                    "При первом запуске необходимо установить локальное Python-окружение (займёт пару минут).\n\n" +
                    "Запустить автоматическую установку сейчас?",
                    "VerbaScribe — Первый запуск",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Information
                );
                if (res != DialogResult.Yes)
                {
                    return;
                }

                ProcessStartInfo setupPsi = new ProcessStartInfo();
                setupPsi.FileName = "cmd.exe";
                setupPsi.Arguments = "/c \"\"" + setupBat + "\"\"";
                setupPsi.WorkingDirectory = baseDir;
                setupPsi.UseShellExecute = true;
                Process setupProc = Process.Start(setupPsi);
                if (setupProc != null)
                {
                    setupProc.WaitForExit();
                }
            }

            if (!File.Exists(venvPython))
            {
                MessageBox.Show(
                    "Окружение .venv не найдено. Пожалуйста, запустите setup.bat вручную.",
                    "VerbaScribe",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning
                );
                return;
            }
        }

        // Launch python.exe run.py with zero console window
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = venvPython;
        psi.Arguments = "\"" + runPy + "\"";
        psi.WorkingDirectory = baseDir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;

        Process app;
        try
        {
            app = Process.Start(psi);
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                "Не удалось запустить VerbaScribe:\n" + ex.Message,
                "Ошибка запуска",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return;
        }
        ShowSplashUntilWindow(app);
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr FindWindow(string cls, string title);
    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr hwnd);

    const string WindowTitle = "VerbaScribe";  // run.py WINDOW_TITLE

    // A small "starting" window: the first start after an update can take a while (Python compiles
    // its files, the speech libraries load), and without it nothing seems to happen.
    // It closes when the app window appears or the app process ends (errors are shown by the app).
    // Light-hearted lines shown while the app starts, a new one every 1.6 s
    static readonly string[] PhrasesRu = {
        "Завариваем чай для нейросетей\u2026",
        "Будим Whisper, он любит поспать\u2026",
        "Разминаем видеокарту\u2026",
        "Расставляем запятые по местам\u2026",
        "Ищем, кто сказал \u00ab\u044d-\u044d\u00bb\u2026",
        "Протираем модели уши\u2026",
        "Распушаем вербу\u2026",
        "Учим нули и единицы слушать\u2026",
        "Раскладываем слова по полочкам\u2026",
        "Настраиваем чуткий слух\u2026",
        "Считаем до десяти на 23 языках\u2026",
        "Подметаем слова-паразиты\u2026",
        "Прогреваем тензоры\u2026",
        "Почти готово, честно\u2026",
    };
    static readonly string[] PhrasesEn = {
        "Brewing tea for the neural nets\u2026",
        "Waking Whisper up, it likes to sleep in\u2026",
        "Stretching the GPU\u2026",
        "Putting commas where they belong\u2026",
        "Finding out who said \u201cumm\u201d\u2026",
        "Cleaning the model's ears\u2026",
        "Fluffing up the willow\u2026",
        "Teaching ones and zeros to listen\u2026",
        "Sorting words onto shelves\u2026",
        "Tuning a keen ear\u2026",
        "Counting to ten in 23 languages\u2026",
        "Sweeping up filler words\u2026",
        "Warming up the tensors\u2026",
        "Almost there, honestly\u2026",
    };

    // The app's UI language, saved by the app in its data folder (default Russian)
    static string UiLanguage()
    {
        try
        {
            string data = Environment.GetEnvironmentVariable("VERBASCRIBE_DATA_DIR");
            if (string.IsNullOrEmpty(data))
                data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VerbaScribe");
            string f = Path.Combine(data, "ui-lang.txt");
            if (File.Exists(f)) return File.ReadAllText(f).Trim();
        }
        catch { }
        return "ru";
    }

    // assets/icon.ico stores PNG images, which System.Drawing.Icon cannot read: take the biggest PNG
    static System.Drawing.Image LoadLogo(string icoPath)
    {
        try
        {
            byte[] d = File.ReadAllBytes(icoPath);
            int count = BitConverter.ToUInt16(d, 4);
            int bestSize = 0, bestOff = 0, bestLen = 0;
            for (int i = 0; i < count; i++)
            {
                int e = 6 + 16 * i;
                int w = d[e] == 0 ? 256 : d[e];
                int len = BitConverter.ToInt32(d, e + 8), off = BitConverter.ToInt32(d, e + 12);
                if (w <= 128 && w > bestSize) { bestSize = w; bestOff = off; bestLen = len; }
            }
            if (bestLen > 0)
                return System.Drawing.Image.FromStream(new MemoryStream(d, bestOff, bestLen));
        }
        catch { }
        return null;
    }

    static void ShowSplashUntilWindow(Process app)
    {
        Application.EnableVisualStyles();
        bool ru = UiLanguage() != "en";
        string[] phrases = ru ? PhrasesRu : PhrasesEn;
        Random rnd = new Random();

        Form splash = new Form();
        splash.FormBorderStyle = FormBorderStyle.None;
        splash.StartPosition = FormStartPosition.CenterScreen;
        splash.Size = new System.Drawing.Size(420, 132);
        splash.BackColor = System.Drawing.Color.FromArgb(28, 28, 30);
        splash.ShowInTaskbar = true;
        splash.TopMost = true;  // a few seconds only: keep it visible over other windows
        splash.Text = "VerbaScribe \u2014 \u0437\u0430\u043f\u0443\u0441\u043a";  // not the app window title: FindWindow must not find the splash
        try { splash.Icon = System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

        // The willow logo, gently bobbing
        PictureBox logo = new PictureBox();
        logo.SizeMode = PictureBoxSizeMode.Zoom;
        logo.SetBounds(22, 32, 64, 64);
        logo.Image = LoadLogo(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "assets", "icon.ico"));

        Label title = new Label();
        title.Text = "VerbaScribe";
        title.ForeColor = System.Drawing.Color.White;
        title.Font = new System.Drawing.Font("Segoe UI Semibold", 13f);
        title.SetBounds(104, 26, 300, 28);
        Label status = new Label();
        status.Text = phrases[rnd.Next(phrases.Length)];
        status.ForeColor = System.Drawing.Color.FromArgb(152, 152, 157);
        status.Font = new System.Drawing.Font("Segoe UI", 9.5f);
        status.SetBounds(104, 58, 300, 22);
        // A thin track with a blue segment running along it (the system progress bar looks foreign here)
        Panel track = new Panel();
        track.BackColor = System.Drawing.Color.FromArgb(52, 52, 56);
        track.SetBounds(104, 92, 290, 3);
        Panel runner = new Panel();
        runner.BackColor = System.Drawing.Color.FromArgb(10, 132, 255);
        runner.SetBounds(0, 0, 80, 3);
        track.Controls.Add(runner);
        splash.Controls.Add(logo);
        splash.Controls.Add(title);
        splash.Controls.Add(status);
        splash.Controls.Add(track);

        DateTime started = DateTime.Now;
        DateTime lastPhrase = DateTime.Now;
        int last = -1;
        Timer timer = new Timer();
        timer.Interval = 40;
        timer.Tick += delegate
        {
            double t = (DateTime.Now - started).TotalSeconds;
            logo.Top = 32 + (int)Math.Round(Math.Sin(t * 2.4) * 3.0);
            runner.Left = (int)(((t * 0.9) % 1.0) * (track.Width + runner.Width)) - runner.Width;
            if ((DateTime.Now - lastPhrase).TotalSeconds >= 1.6)
            {
                int next = rnd.Next(phrases.Length);
                if (next == last) next = (next + 1) % phrases.Length;
                last = next;
                status.Text = phrases[next];
                lastPhrase = DateTime.Now;
            }
            IntPtr hwnd = FindWindow(null, WindowTitle);
            bool done = (hwnd != IntPtr.Zero && IsWindowVisible(hwnd)) || app.HasExited || t > 180;
            if (done)
            {
                timer.Stop();
                splash.Close();
            }
        };
        timer.Start();
        Application.Run(splash);
    }
}
"""


def compile_exe() -> Path:
    build_ico(ICO_PATH)
    print(f"[OK] Willow icon generated: {ICO_PATH} ({ICO_PATH.stat().st_size} bytes)")

    cs_path = BASE_DIR / "launcher.cs"
    cs_path.write_text(CS_SOURCE.strip(), encoding="utf-8-sig")

    csc_candidates = [
        r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
        r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe",
    ]
    csc = next((p for p in csc_candidates if os.path.exists(p)), None)
    if not csc:
        raise RuntimeError("csc.exe not found")

    exe_path = BASE_DIR / "VerbaScribe.exe"
    cmd = [
        csc,
        "/nologo",
        "/target:winexe",
        "/optimize+",
        f"/out:{exe_path}",
        f"/win32icon:{ICO_PATH}",
        "/r:System.Windows.Forms.dll",
        "/r:System.Drawing.dll",
        str(cs_path),
    ]
    res = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Compilation failed:\n{res.stdout}\n{res.stderr}")

    try:
        cs_path.unlink()
    except Exception:
        pass

    print(f"[OK] Compiled native executable: {exe_path} ({exe_path.stat().st_size} bytes)")
    return exe_path


if __name__ == "__main__":
    compile_exe()
