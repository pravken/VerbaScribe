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


def make_png(size: int) -> bytes:
    """
    Generates an RGBA PNG image of size x size with an Apple macOS squircle background
    (#0A84FF to #5E5CE6 gradient) and a clean studio audio waveform / mic icon in white.
    Uses pure Python (zlib + struct) with zero external dependencies.
    """
    raw_rows = bytearray()
    cx = (size - 1) / 2.0
    cy = (size - 1) / 2.0
    half = size * 0.44
    corner_r = size * 0.22

    # Waveform bar x positions (normalized -1..1) and heights (normalized 0..1)
    bars = [
        (-0.52, 0.22),
        (-0.26, 0.48),
        (0.00, 0.72),
        (0.26, 0.52),
        (0.52, 0.26),
    ]
    bar_half_w = 0.068

    for y in range(size):
        raw_rows.append(0)  # PNG filter type 0 (None)
        ny = (y - cy) / half
        t_grad = y / max(1, size - 1)

        # Gradient colors: top (#1A8CFF) -> bottom (#4B48E6)
        bg_r = int(26 * (1 - t_grad) + 75 * t_grad)
        bg_g = int(140 * (1 - t_grad) + 72 * t_grad)
        bg_b = int(255 * (1 - t_grad) + 230 * t_grad)

        for x in range(size):
            nx = (x - cx) / half

            # Rounded rectangle SDF
            dx = max(abs(x - cx) - (half - corner_r), 0.0)
            dy = max(abs(y - cy) - (half - corner_r), 0.0)
            dist = math.hypot(dx, dy) - corner_r

            if dist > 0.8:
                raw_rows.extend((0, 0, 0, 0))
                continue

            # Anti-aliased edge alpha
            bg_alpha = 1.0 if dist < -0.8 else max(0.0, min(1.0, 0.5 - dist / 1.6))

            # Subtle inner top highlight
            if dist > -size * 0.03 and y < cy:
                bg_r_px = min(255, bg_r + 22)
                bg_g_px = min(255, bg_g + 22)
                bg_b_px = min(255, bg_b + 22)
            else:
                bg_r_px, bg_g_px, bg_b_px = bg_r, bg_g, bg_b

            # Check if pixel is inside one of the 5 rounded voice waveform bars
            fg_alpha = 0.0
            for bx, bh in bars:
                bdx = max(abs(nx - bx) - (bar_half_w * 0.2), 0.0)
                bdy = max(abs(ny) - (bh - bar_half_w), 0.0)
                bdist = (math.hypot(bdx, bdy) - bar_half_w * 0.85) * half
                if bdist < 0.8:
                    a = 1.0 if bdist < -0.8 else max(0.0, min(1.0, 0.5 - bdist / 1.6))
                    fg_alpha = max(fg_alpha, a)

            r = int(bg_r_px * (1 - fg_alpha) + 255 * fg_alpha)
            g = int(bg_g_px * (1 - fg_alpha) + 255 * fg_alpha)
            b = int(bg_b_px * (1 - fg_alpha) + 255 * fg_alpha)
            a = int(bg_alpha * 255)
            raw_rows.extend((r, g, b, a))

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
using System.Windows.Forms;

class WhisperStudioLauncher
{
    [STAThread]
    static void Main(string[] args)
    {
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

        // Launch python.exe run.py with zero console window!
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = venvPython;
        psi.Arguments = "\"" + runPy + "\"";
        psi.WorkingDirectory = baseDir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;

        try
        {
            Process.Start(psi);
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                "Не удалось запустить VerbaScribe:\n" + ex.Message,
                "Ошибка запуска",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
        }
    }
}
"""


def compile_exe() -> Path:
    import shutil

    build_ico(ICO_PATH)
    print(f"[OK] Icon generated: {ICO_PATH} ({ICO_PATH.stat().st_size} bytes)")

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
        str(cs_path),
    ]
    res = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Compilation failed:\n{res.stdout}\n{res.stderr}")

    try:
        cs_path.unlink()
    except Exception:
        pass

    # Also keep WhisperStudio.exe updated
    try:
        shutil.copy2(exe_path, BASE_DIR / "WhisperStudio.exe")
    except Exception:
        pass

    print(f"[OK] Compiled native executable: {exe_path} ({exe_path.stat().st_size} bytes)")
    return exe_path


if __name__ == "__main__":
    compile_exe()
