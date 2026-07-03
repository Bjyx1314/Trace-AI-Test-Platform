$ErrorActionPreference = 'Stop'

# 该脚本自身包含用于拦截的关键词，因此必须从内容扫描中排除。
$exclude = @(
  'frontend/package-lock.json',
  'scripts/check-secrets.ps1'
)

$patterns = @(
  '192\.168\.',
  '10\.[0-9]+\.[0-9]+\.[0-9]+',
  '172\.(1[6-9]|2[0-9]|3[01])\.',
  'sk-[A-Za-z0-9_-]{16,}',
  'AKIA[0-9A-Z]{16}',
  'ghp_[A-Za-z0-9]{20,}',
  'github_pat_[A-Za-z0-9_]{20,}',
  'xox[baprs]-[A-Za-z0-9-]{10,}',
  '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----',
  '[A-Za-z]:\\',
  '(?i)agent[-_ ]board',
  '(?i)\bcrm\b',
  '拜访',
  '(?i)/customer(?:/|\b)',
  '(?i)app\.x\.com',
  '(?i)同步上游[^\r\n]*[0-9a-f]{7,40}'
)

if ($env:OPEN_SOURCE_FORBIDDEN_PATTERNS) {
  $patterns += $env:OPEN_SOURCE_FORBIDDEN_PATTERNS.Split(';') |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
}

$failed = $false
foreach ($pattern in $patterns) {
  $args = @(
    '-n', '-I', '--hidden',
    '-g', '!.git/**',
    '-g', '!**/__pycache__/**',
    '-g', '!frontend/node_modules/**',
    '-g', '!frontend/dist/**',
    '-g', '!build/**',
    '-g', '!dist/**'
  )
  foreach ($item in $exclude) { $args += @('-g', "!$item") }
  $args += '--'
  $result = & rg @args $pattern . 2>$null
  if ($LASTEXITCODE -eq 0 -and $result) {
    Write-Host "Potential private or business-specific content matched: $pattern" -ForegroundColor Red
    $result | Write-Host
    $failed = $true
  }
}

# Git 元数据不会被普通文件扫描覆盖，公开仓库必须使用匿名或 noreply 邮箱。
$allowedCommitEmail = '^[^@]+@users\.noreply\.github\.com$'
$commitEmails = @(& git log --all --format='%ae%n%ce' 2>$null) |
  Where-Object { $_ } |
  Sort-Object -Unique
foreach ($email in $commitEmails) {
  if ($email -notmatch $allowedCommitEmail) {
    Write-Host "Non-public Git author/committer email detected: $email" -ForegroundColor Red
    $failed = $true
  }
}

if ($failed) { exit 1 }
Write-Host 'Open-source privacy and secret scan passed.' -ForegroundColor Green
