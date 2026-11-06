#define MyAppName "SchoolPass"
#define MyAppVersion "4.9.4"
#define MyAppPublisher "SchoolPass"

[Setup]
AppId={{9A793D9C-9A32-4F09-8B72-6CE5BA2D61F4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/boufatis-school-pass/SchoolPass
SetupIconFile=app.ico
DefaultDirName={localappdata}\Programs\SchoolPass
DefaultGroupName=SchoolPass
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=SchoolPass_Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
UninstallDisplayName=SchoolPass
CloseApplications=yes
RestartApplications=no
Uninstallable=yes
ChangesAssociations=no
VersionInfoVersion=4.9.4.0
VersionInfoProductVersion=4.9.4
VersionInfoProductName=SchoolPass
VersionInfoCompany=SchoolPass

[Tasks]
Name: "desktopicon"; Description: "إنشاء اختصار على سطح المكتب"; GroupDescription: "الاختصارات:"; Flags: unchecked

[Files]
Source: "dist\SchoolPass\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "app.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "VERSION.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "قالب_قائمة_التلاميذ.csv"; DestDir: "{app}"; Flags: ignoreversion
Source: "دليل_الاستخدام.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\SchoolPass"; Filename: "{app}\SchoolPass.exe"; IconFilename: "{app}\app.ico"
Name: "{autodesktop}\SchoolPass"; Filename: "{app}\SchoolPass.exe"; IconFilename: "{app}\app.ico"; Tasks: desktopicon
Name: "{autoprograms}\دليل استخدام SchoolPass"; Filename: "{app}\دليل_الاستخدام.txt"

[Run]
Filename: "{app}\SchoolPass.exe"; Description: "تشغيل SchoolPass الآن"; Flags: postinstall nowait skipifsilent

; قاعدة البيانات محفوظة خارج مجلد التثبيت في %LOCALAPPDATA%\SchoolPass.
; لا توجد تعليمات حذف أو استبدال لقاعدة البيانات أثناء التثبيت أو التحديث أو الإزالة.
