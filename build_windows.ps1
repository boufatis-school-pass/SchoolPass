$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$PythonVersion = '3.14.10'
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"

function Say([string]$Message) { Write-Host "`n=== $Message ===" -ForegroundColor Cyan }
function Invoke-NativeChecked([string]$Executable, [string[]]$Arguments, [string]$FailureMessage) {
    $oldPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $Executable @Arguments 2>&1
        $exitCode = $LASTEXITCODE
        foreach ($line in $output) { Write-Host $line }
    } finally { $ErrorActionPreference = $oldPreference }
    if ($exitCode -ne 0) { throw "$FailureMessage (رمز الخروج: $exitCode)" }
}
function Find-Python {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source) {
        try {
            $candidate = $pythonCommand.Source
            $identity = & $candidate -c "import sys,struct; print('{}.{} {}'.format(sys.version_info.major,sys.version_info.minor,struct.calcsize('P')*8))" 2>$null
            if ($identity -match '^3\.(13|14) 64$') { return $candidate }
        } catch {}
    }
    foreach ($v in @('3.14','3.13')) {
        try {
            $p = (& py "-$v" -c "import sys,struct; print(sys.executable if struct.calcsize('P')==8 and sys.version_info[:2] in ((3,14),(3,13)) else '')" 2>$null | Select-Object -Last 1)
            if ($LASTEXITCODE -eq 0 -and $p -and (Test-Path $p.Trim())) { return $p.Trim() }
        } catch {}
    }
    $base = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path $base) {
        $candidate = Get-ChildItem -Path $base -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match 'Python31[34]\\python\.exe$' } | Select-Object -First 1
        if ($candidate) {
            $identity = & $candidate.FullName -c "import sys,struct; print('{}.{} {}'.format(sys.version_info.major,sys.version_info.minor,struct.calcsize('P')*8))"
            if ($identity -match '^3\.(13|14) 64$') { return $candidate.FullName }
        }
    }
    return $null
}
function Assert-Authenticode([string]$Path, [string]$Label) {
    if (-not (Test-Path $Path) -or (Get-Item $Path).Length -lt 1000000) { throw "$Label installer is missing or unexpectedly small." }
    $sig = Get-AuthenticodeSignature -FilePath $Path
    if ($sig.Status -ne 'Valid') { throw "$Label signature is not valid (status: $($sig.Status)); refusing to run the installer." }
}

try {
    Say 'التحقق من Python 64-bit'
    $Python = Find-Python
    if (-not $Python) {
        Say 'تنزيل Python الرسمي وتثبيته للمستخدم الحالي'
        $Installer = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
        Invoke-WebRequest -UseBasicParsing -Uri $PythonUrl -OutFile $Installer -TimeoutSec 120
        Assert-Authenticode $Installer 'Python'
        $proc = Start-Process -FilePath $Installer -ArgumentList @('/quiet','InstallAllUsers=0','PrependPath=1','Include_launcher=1','Include_test=0','InstallLauncherAllUsers=0','Include_pip=1','Include_tcltk=1') -Wait -PassThru
        if ($proc.ExitCode -ne 0) { throw "لم يكتمل تثبيت Python. رمز الخروج: $($proc.ExitCode)" }
        $Python = Find-Python
        if (-not $Python) { throw 'تعذر العثور على Python 64-bit بعد التثبيت. أعد تشغيل Windows ثم أعد المحاولة.' }
    }
    $arch = & $Python -c "import struct; print(struct.calcsize('P')*8)"
    if ($arch -ne '64') { throw 'يلزم Python 64-bit لبناء برنامج Windows.' }
    Write-Host "Python: $Python"

    Say 'تثبيت متطلبات البناء والاختبار'
    Invoke-NativeChecked $Python @('-m','pip','install','--upgrade','pip') 'تعذر تحديث pip.'
    Invoke-NativeChecked $Python @('-m','pip','install','--disable-pip-version-check','-r',(Join-Path $Root 'requirements_dev.txt')) 'تعذر تثبيت مكتبات البناء.'

    Say 'فحوص المصدر والترميز والعربية'
    Invoke-NativeChecked $Python @((Join-Path $Root 'audit_arabic_text.py')) 'فشل فحص اللغة والترميز.'
    Invoke-NativeChecked $Python @((Join-Path $Root 'release_preflight.py')) 'فشل فحص اتساق الإصدار وملفات المستودع.'
    Invoke-NativeChecked $Python @((Join-Path $Root 'preflight.py')) 'فشل الفحص المسبق للمكتبات والخط العربي.'
    Invoke-NativeChecked $Python @('-m','compileall','-q',(Join-Path $Root 'app.py'),(Join-Path $Root 'schoolpass_core.py')) 'فشل فحص صياغة Python.'

    Say 'تشغيل اختبارات الانحدار'
    Invoke-NativeChecked $Python @('-m','pytest','-q') 'فشل اختبار واحد أو أكثر؛ أوقفنا بناء نسخة التثبيت.'

    Say 'بناء النسخة التنفيذية'
    foreach ($folder in @('dist','build')) {
        $path = Join-Path $Root $folder
        if (Test-Path $path) { Remove-Item $path -Recurse -Force }
    }
    $args = @('-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed','--name','SchoolPass',
        '--icon',(Join-Path $Root 'app.ico'),'--add-data',((Join-Path $Root 'app.ico') + ';.'),
        '--collect-all','qrcode','--collect-all','reportlab','--collect-all','ttkbootstrap','--collect-all','PIL','--collect-all','arabic_reshaper',
        '--collect-all','bidi','--collect-all','openpyxl','--hidden-import','xlrd','--hidden-import','schoolpass_core','app.py')
    Invoke-NativeChecked $Python $args 'فشل بناء SchoolPass.exe.'
    $portable = Join-Path $Root 'dist\SchoolPass\SchoolPass.exe'
    if (-not (Test-Path $portable) -or (Get-Item $portable).Length -lt 100000) {
        throw 'لم ينتج PyInstaller ملف SchoolPass.exe صالحًا.'
    }

    Say 'التحقق من تحليل سكريبت المثبّت قبل تشغيله'
    $InstallerScript = Join-Path $Root 'build_installer_only.ps1'
    $tokens = $null; $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($InstallerScript, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -gt 0) {
        $details = ($parseErrors | ForEach-Object { $_.Message + ' (line ' + $_.Extent.StartLineNumber + ')' }) -join '; '
        throw "Installer PowerShell parse check failed: $details"
    }

    Say 'إنشاء المثبّت والتحقق منه'
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $InstallerScript
    if ($LASTEXITCODE -ne 0) { throw 'فشلت مرحلة إنشاء المثبّت. احتفظ بمخرجات الخطأ وأرسلها للمراجعة.' }
    $setup = Join-Path $Root 'dist\SchoolPass_Setup.exe'
    if (-not (Test-Path $setup) -or (Get-Item $setup).Length -lt 1000000) { throw 'لم يتم العثور على مثبت Windows صالح.' }

    Write-Host "`nاكتمل بناء SchoolPass 4.7.2." -ForegroundColor Green
    Write-Host "ملف التثبيت: $setup" -ForegroundColor Green
    Write-Host 'لم يجر لمس مجلد بيانات المستخدم %LOCALAPPDATA%\SchoolPass.'
    Start-Process explorer.exe -ArgumentList (Join-Path $Root 'dist')
    exit 0
} catch {
    Write-Host "`nفشل البناء: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'لم يتم حذف قاعدة بيانات البرنامج. أرسل رسالة الخطأ كاملة عند طلب الدعم.' -ForegroundColor Yellow
    exit 1
}
