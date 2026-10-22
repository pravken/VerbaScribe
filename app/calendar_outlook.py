"""
The calendar of the classic Outlook installed on this computer (Microsoft 365 / Exchange / Outlook.com:
the same calendar Teams shows its meetings in). Read through Outlook's own automation interface with
the PowerShell that comes with Windows: no link to publish, no sign-in, nothing leaves the computer.

The new Outlook ("Outlook (new)") and Outlook on the web have no such interface: for them the calendar
is added by its published .ics link (app/calendar_ics.py).

Selected in Settings -> Integrations -> Calendar as the source "outlook". Recurring meetings come
already expanded by Outlook; cancelled ones and all-day events are left out.
"""

import json
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List

SOURCE = "outlook"
DAYS_BACK = 1
DAYS_AHEAD = 14
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Outlook's Restrict wants the dates in the user's own short format, which PowerShell's ToString('g') gives.
# MeetingStatus 5 / 7: the meeting was cancelled (by the organizer / received cancelled).
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ol = New-Object -ComObject Outlook.Application
$ns = $ol.GetNamespace('MAPI')
$me = ''
try { $me = $ns.CurrentUser.Name } catch {}
$items = $ns.GetDefaultFolder(9).Items
$items.IncludeRecurrences = $true
$items.Sort('[Start]')
$from = (Get-Date).Date.AddDays(-__BACK__).ToString('g')
$to = (Get-Date).Date.AddDays(__AHEAD__).ToString('g')
$found = $items.Restrict("[Start] >= '$from' AND [Start] <= '$to'")
$out = @()
foreach ($a in $found) {
  if ($a.AllDayEvent) { continue }
  if ($a.MeetingStatus -eq 5 -or $a.MeetingStatus -eq 7) { continue }
  $people = @()
  foreach ($r in @($a.RequiredAttendees, $a.OptionalAttendees)) {
    if ($r) { $people += ($r -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -ne $me }) }
  }
  $out += [pscustomobject]@{
    uid = [string]$a.GlobalAppointmentID
    title = [string]$a.Subject
    start = $a.Start.ToString('yyyy-MM-ddTHH:mm:ss')
    end = $a.End.ToString('yyyy-MM-ddTHH:mm:ss')
    location = [string]$a.Location
    attendees = @($people)
  }
  if ($out.Count -ge 300) { break }
}
ConvertTo-Json -InputObject @($out) -Depth 4 -Compress
"""


def supported() -> bool:
    """Classic Outlook is registered for automation on this computer."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Outlook.Application\CLSID"):
            return True
    except OSError:
        return False


def fetch() -> List[Dict[str, Any]]:
    """Meetings from yesterday to two weeks ahead, as app/calendar_ics.py events."""
    if not supported():
        raise RuntimeError("Классический Outlook на этом компьютере не найден. Для нового Outlook и Outlook в браузере "
                           "добавьте опубликованную ссылку .ics")
    script = _SCRIPT.replace("__BACK__", str(DAYS_BACK)).replace("__AHEAD__", str(DAYS_AHEAD))
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                             creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Outlook не ответил за 90 секунд (возможно, ждёт выбора профиля или пароля)")
    if res.returncode != 0:
        raise RuntimeError("Outlook: " + ((res.stderr or res.stdout or "").strip().splitlines() or ["ошибка"])[-1][:200])
    return parse(res.stdout)


def parse(text: str) -> List[Dict[str, Any]]:
    data = json.loads((text or "").strip() or "[]")
    if isinstance(data, dict):  # PowerShell 5 gives one object instead of a list of one
        data = [data]
    out = []
    for it in data or []:
        try:
            start = datetime.fromisoformat(str(it["start"]))
            end = datetime.fromisoformat(str(it["end"]))
        except (KeyError, ValueError):
            continue
        attendees = it.get("attendees") or []
        if isinstance(attendees, str):
            attendees = [attendees]
        out.append({"uid": str(it.get("uid") or ""), "title": str(it.get("title") or "").strip(), "start": start,
                    "end": end if end > start else start, "attendees": [str(a) for a in attendees if str(a).strip()],
                    "rrule": "", "exdates": [], "all_day": False, "status": ""})
    return out
