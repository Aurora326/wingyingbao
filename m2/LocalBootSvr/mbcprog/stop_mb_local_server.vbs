Set WshShell = CreateObject("WScript.Shell") 
WshShell.Run "net stop MbiCltSvr",0
WScript.quit
