@echo off
cd /d "%~dp0"
echo 启动数字主持人服务...
start /min python server.py
echo 服务已启动，端口 8766
echo 访问地址: http://192.168.0.102:8766
pause