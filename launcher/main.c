#define UNICODE
#define _UNICODE
#include <windows.h>
#include <stdio.h>
#include "payload.h"

static int write_file(const wchar_t *path, const unsigned char *bytes, DWORD count) {
    HANDLE file=CreateFileW(path,GENERIC_WRITE,0,NULL,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);
    if(file==INVALID_HANDLE_VALUE) return 0;
    DWORD written=0; BOOL ok=WriteFile(file,bytes,count,&written,NULL);
    if(!CloseHandle(file)) return 0;
    return ok && written==count;
}
static int ps_quote(const wchar_t *source,wchar_t *dest,size_t capacity) {
    size_t out=0;
    for(size_t i=0;source[i];i++) {
        if(out+3>=capacity) return 0;
        if(source[i]==L'\'') dest[out++]=L'\'';
        dest[out++]=source[i];
    }
    dest[out]=0;return 1;
}
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE previous,LPWSTR args,int show) {
    (void)instance;(void)previous;(void)show;
    BOOL self_test=wcscmp(args,L"--self-test")==0;
    HANDLE mutex=CreateMutexW(NULL,TRUE,L"Local\\LocalGamePilot-Launcher");
    if(!mutex || GetLastError()==ERROR_ALREADY_EXISTS) {
        if(!self_test) MessageBoxW(NULL,L"Local Game Pilot is already running.",L"Local Game Pilot",MB_OK|MB_ICONINFORMATION);
        if(mutex) CloseHandle(mutex);return 1;
    }
    wchar_t temp[MAX_PATH],dir[MAX_PATH],script[MAX_PATH],zip[MAX_PATH],log[MAX_PATH];
    wchar_t system[MAX_PATH],powershell[MAX_PATH],command[MAX_PATH*8];
    wchar_t quoted_script[MAX_PATH*2],quoted_zip[MAX_PATH*2];
    int result=1;
    if(!GetTempPathW(MAX_PATH,temp) || !GetTempFileNameW(temp,L"gpk",0,dir)) goto failed;
    if(!DeleteFileW(dir) || !CreateDirectoryW(dir,NULL)) goto failed;
    if(swprintf(script,MAX_PATH,L"%ls\\bootstrap.ps1",dir)<0 || swprintf(zip,MAX_PATH,L"%ls\\application.zip",dir)<0 || swprintf(log,MAX_PATH,L"%ls\\bootstrap.log",dir)<0) goto failed;
    if(!write_file(script,bootstrap_ps1,(DWORD)sizeof(bootstrap_ps1)) || !write_file(zip,application_zip,(DWORD)sizeof(application_zip))) goto failed;
    if(!GetSystemDirectoryW(system,MAX_PATH)) goto failed;
    if(swprintf(powershell,MAX_PATH,L"%ls\\WindowsPowerShell\\v1.0\\powershell.exe",system)<0) goto failed;
    if(!ps_quote(script,quoted_script,MAX_PATH*2) || !ps_quote(zip,quoted_zip,MAX_PATH*2)) goto failed;
    /* Command mode runs our embedded UI without requiring a machine-wide script policy change. */
    if(swprintf(command,MAX_PATH*8,L"\"%ls\" -NoLogo -NoProfile -NonInteractive -STA -Command \"& ([scriptblock]::Create([IO.File]::ReadAllText('%ls'))) -Payload '%ls' %ls\"",powershell,quoted_script,quoted_zip,self_test?L"-SelfTest":L"")<0) goto failed;
    STARTUPINFOW startup={0}; PROCESS_INFORMATION process={0};
    startup.cb=sizeof(startup);
    SECURITY_ATTRIBUTES security={sizeof(security),NULL,TRUE};
    HANDLE output=CreateFileW(log,GENERIC_WRITE,FILE_SHARE_READ,&security,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);
    if(output==INVALID_HANDLE_VALUE) goto failed;
    HANDLE input=CreateFileW(L"NUL",GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,&security,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,NULL);
    if(input==INVALID_HANDLE_VALUE) { CloseHandle(output);goto failed; }
    startup.dwFlags=STARTF_USESTDHANDLES;
    startup.hStdOutput=output;startup.hStdError=output;startup.hStdInput=input;
    BOOL created=CreateProcessW(powershell,command,NULL,NULL,TRUE,CREATE_NO_WINDOW,NULL,dir,&startup,&process);
    CloseHandle(output);CloseHandle(input);
    if(!created) goto failed;
    WaitForSingleObject(process.hProcess,INFINITE);
    DWORD exitcode=1;GetExitCodeProcess(process.hProcess,&exitcode);
    CloseHandle(process.hThread);CloseHandle(process.hProcess);
    if(exitcode) {
        wchar_t message[MAX_PATH*2];
        swprintf(message,MAX_PATH*2,L"Setup could not start or finish. Details:\n%ls\n\nWindows PowerShell 5.1 and Windows 10/11 x64 are required. Organization execution policies are respected.",log);
        if(!self_test) MessageBoxW(NULL,message,L"Local Game Pilot",MB_OK|MB_ICONERROR);
    } else { DeleteFileW(log);result=0; }
    DeleteFileW(script);DeleteFileW(zip);
    if(!result) RemoveDirectoryW(dir);
    ReleaseMutex(mutex);CloseHandle(mutex);
    return result;
failed:
    if(!self_test) MessageBoxW(NULL,L"Unable to prepare setup files or start Windows PowerShell.",L"Local Game Pilot",MB_OK|MB_ICONERROR);
    ReleaseMutex(mutex);CloseHandle(mutex);return 1;
}
