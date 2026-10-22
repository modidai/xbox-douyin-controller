' Hidden launcher for xbox-douyin-controller (used by Windows Startup).
' Log is appended to run.log in the project root; check it when something is wrong.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
sh.CurrentDirectory = base
sh.Run "cmd /c python -u run.py >> """ & base & "\run.log"" 2>&1", 0, False
