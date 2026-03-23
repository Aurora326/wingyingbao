Set WshShell = CreateObject("WScript.Shell") 
WshShell.Run "net start MbiCltSvr", 0
WScript.quit
