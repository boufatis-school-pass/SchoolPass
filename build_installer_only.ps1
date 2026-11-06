$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Say([string]$Message) { Write-Host "`n=== $Message ===" -ForegroundColor Cyan }
function Find-ISCC {
    $candidates = [System.Collections.Generic.List[string]]::new()
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { $candidates.Add($cmd.Source) }
    $roots = @(
        (Join-Path $env:LOCALAPPDATA 'Programs'),
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        (Join-Path $env:LOCALAPPDATA 'Apps')
    ) | Where-Object { $_ -and (Test-Path $_) }
    foreach ($base in $roots) {
        foreach ($folder in @('Inno Setup 7','Inno Setup 6','Inno Setup')) {
            $candidate = Join-Path $base (Join-Path $folder 'ISCC.exe')
            if (Test-Path $candidate) { $candidates.Add($candidate) }
        }
        try {
            Get-ChildItem -Path $base -Directory -Filter 'Inno Setup*' -ErrorAction SilentlyContinue |
                ForEach-Object {
                    $candidate = Join-Path $_.FullName 'ISCC.exe'
                    if (Test-Path $candidate) { $candidates.Add($candidate) }
                }
        } catch {}
    }
    # Registry paths cover non-default custom installations.
    foreach ($key in @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )) {
        try {
            Get-ItemProperty $key -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -like 'Inno Setup*' -and $_.InstallLocation } |
                ForEach-Object {
                    $candidate = Join-Path $_.InstallLocation 'ISCC.exe'
                    if (Test-Path $candidate) { $candidates.Add($candidate) }
                }
        } catch {}
    }
    return ($candidates | Select-Object -Unique | Where-Object { Test-Path $_ } | Select-Object -First 1)
}
function Invoke-Compiler([string]$Compiler, [string]$Script) {
    $old = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $Compiler /Qp $Script 2>&1
        $code = $LASTEXITCODE
        foreach ($line in $output) { Write-Host $line }
    } finally { $ErrorActionPreference = $old }
    if ($code -ne 0) { throw "Inno Setup failed with exit code $code." }
}
function Install-InnoSetup {
    $installer = Join-Path $env:TEMP 'SchoolPass_InnoSetup_Installer.exe'
    $url = 'https://jrsoftware.org/download.php/is.exe'
    Say 'تنزيل مُجمّع Inno Setup الرسمي'
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installer -TimeoutSec 90
    if (-not (Test-Path $installer) -or (Get-Item $installer).Length -lt 1000000) {
        throw 'ملف Inno Setup الذي تم تنزيله مفقود أو أصغر من الحجم المتوقع؛ لن يتم تشغيله.'
    }
    $signature = Get-AuthenticodeSignature -FilePath $installer
    if ($signature.Status -ne 'Valid') {
        throw "توقيع Inno Setup غير صالح (الحالة: $($signature.Status)). أوقفنا التثبيت للحماية."
    }
    $proc = Start-Process -FilePath $installer -ArgumentList @('/CURRENTUSER','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-') -Wait -PassThru
    if ($proc.ExitCode -ne 0) { throw "انتهى مثبت Inno Setup برمز $($proc.ExitCode)." }
    Start-Sleep -Seconds 2
}

try {
    Say 'التحقق من ملفات النسخة المحمولة الموجودة'
    $appExe = Join-Path $Root 'dist\SchoolPass\SchoolPass.exe'
    $iss = Join-Path $Root 'SchoolPass_Setup.iss'
    if (-not (Test-Path $appExe) -or (Get-Item $appExe).Length -lt 100000) {
        throw 'لم أجد dist\SchoolPass\SchoolPass.exe صالحًا. أنشئ النسخة المحمولة أولًا بواسطة build_windows.ps1.'
    }
    if (-not (Test-Path $iss)) { throw 'ملف SchoolPass_Setup.iss غير موجود.' }
    foreach ($asset in @('app.ico','VERSION.txt','قالب_قائمة_التلاميذ.csv','دليل_الاستخدام.txt')) {
        if (-not (Test-Path (Join-Path $Root $asset))) { throw "ملف التثبيت المطلوب مفقود: $asset" }
    }

    Say 'العثور على مترجم Inno Setup'
    $compiler = Find-ISCC
    if (-not $compiler) {
        try { Install-InnoSetup } catch {
            throw "تعذر تجهيز Inno Setup تلقائيًا: $($_.Exception.Message) `nثبّت Inno Setup 6 أو 7 من الموقع الرسمي ثم شغّل build_installer_only.bat. لم يتم حذف النسخة المحمولة أو بياناتك."
        }
        $compiler = Find-ISCC
    }
    if (-not $compiler) {
        throw 'لم يُعثر على ISCC.exe بعد التثبيت. أعد تشغيل Windows أو ثبّت Inno Setup 6/7 ثم أعد المحاولة.'
    }
    Write-Host "مترجم Inno Setup: $compiler"

    $output = Join-Path $Root 'dist\SchoolPass_Setup.exe'
    if (Test-Path $output) { Remove-Item $output -Force }
    $started = Get-Date
    Say 'إنشاء ملف التثبيت'
    Invoke-Compiler $compiler $iss

    if (-not (Test-Path $output)) { throw 'انتهى ISCC دون إنشاء dist\SchoolPass_Setup.exe.' }
    $file = Get-Item $output
    if ($file.Length -lt 1000000) { throw "ملف المثبّت غير مكتمل أو صغير جدًا: $($file.Length) bytes." }
    if ($file.LastWriteTime -lt $started.AddSeconds(-2)) { throw 'تاريخ ملف المثبّت لا يثبت أنه أُنشئ في هذه العملية؛ تم رفض نتيجة البناء.' }
    $stream = [System.IO.File]::OpenRead($output)
    try {
        $header = New-Object byte[] 2
        [void]$stream.Read($header, 0, 2)
        if ($header[0] -ne 0x4D -or $header[1] -ne 0x5A) { throw 'الملف الناتج ليس ملف Windows EXE صالح الترويسة.' }
    } finally { $stream.Dispose() }

    $hash = (Get-FileHash -Algorithm SHA256 -Path $output).Hash.ToLowerInvariant()
    Set-Content -Path ($output + '.sha256') -Value ("$hash  SchoolPass_Setup.exe") -Encoding ASCII
    Write-Host "`nنجاح: تم إنشاء المثبّت والتحقق من وجوده وحجمه وترويسة EXE." -ForegroundColor Green
    Write-Host "المسار الكامل: $output" -ForegroundColor Green
    exit 0
} catch {
    Write-Host "`nفشل إنشاء المثبّت: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
