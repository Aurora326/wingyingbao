@ECHO OFF
cd /d %~dp0
echo.
echo 导入驱动测试签名证书为受信证书
certutil.exe -f -addstore Root AiKeyboard.cer >nul
certutil.exe -f -addstore TrustedPublisher AiKeyboard.cer >nul
if %errorlevel% equ 0 (
  echo 证书导入成功。
) else (
  echo 证书导入失败。
)