' Launches run_background.bat with NO visible console window - this is the
' standard trick for a silent Task Scheduler action (schtasks.exe has no
' "hidden window" flag of its own). WScript.Shell.Run's 3rd arg (0) is the
' hidden window style; the 4th (False) means do not wait for it to exit,
' since run_background.bat loops forever by design.
Set WshShell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.Run """" & scriptDir & "\run_background.bat""", 0, False
