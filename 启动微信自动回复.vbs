Option Explicit

Dim fso, shell, root, script, py, pyw, rc, quote, setup
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
quote = Chr(34)
root = fso.GetParentFolderName(WScript.ScriptFullName)
script = root & "\wechat_auto_reply.py"
py = root & "\.venv\Scripts\python.exe"
pyw = root & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(script) Then
  MsgBox "wechat_auto_reply.py not found in:" & vbCrLf & root, 16, "wx-auto"
  WScript.Quit 1
End If

If Not fso.FileExists(py) Then
  rc = 1
Else
  rc = shell.Run("cmd /c " & quote & quote & py & quote & " -c ""import tkinter, wechatauto"" >nul 2>&1" & quote, 0, True)
End If

If rc <> 0 Then
  setup = root & "\install_dependencies.bat"
  If Not fso.FileExists(setup) Then
    MsgBox "install_dependencies.bat not found in:" & vbCrLf & root, 16, "wx-auto"
    WScript.Quit 1
  End If
  rc = shell.Run("cmd /c " & quote & quote & setup & quote, 1, True)
  If rc <> 0 Then
    MsgBox "Dependency installation failed. Run install_dependencies.bat manually for details.", 16, "wx-auto"
    WScript.Quit 1
  End If
End If

If fso.FileExists(pyw) Then
  py = pyw
End If

shell.CurrentDirectory = root
shell.Run quote & py & quote & " " & quote & script & quote, 0, False
