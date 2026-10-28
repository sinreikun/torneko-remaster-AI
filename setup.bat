@echo off
:menu
cls
echo Local Game Pilot - Runtime selection
echo.
echo 1. Python official downloads (Python 3.13 or newer with Tcl/Tk and pip)
echo 2. Ollama official download (recommended local vision server)
echo 3. llama.cpp official releases (CPU / CUDA / Vulkan)
echo 4. LM Studio official download (GUI model management)
echo 5. Start tool / choose vision models and download inside the tool
 echo 0. Exit
set /p choice=Select:
if "%choice%"=="1" start "" "https://www.python.org/downloads/windows/"
if "%choice%"=="2" start "" "https://ollama.com/download/windows"
if "%choice%"=="3" start "" "https://github.com/ggml-org/llama.cpp/releases"
if "%choice%"=="4" start "" "https://lmstudio.ai/download"
if "%choice%"=="5" call "%~dp0start.bat"
if "%choice%"=="0" exit /b
 goto menu
