# Install killer-skills into ~/.claude/skills, from the latest release.
#
#   irm https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.ps1 | iex
#
# Name skills to install only those:
#
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.ps1))) killer-repo
#
# Each archive is checked against the release's SHA256SUMS.txt before anything
# is replaced. A skill that's already installed is replaced whole, so files a
# newer version dropped don't linger. Windows PowerShell 5.1 and PowerShell 7.
#
#   $env:KILLER_SKILLS_VERSION = 'v1.0.0'   a release tag instead of the latest
#   $env:KILLER_SKILLS_DIR = 'path'         install somewhere other than ~/.claude/skills
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Skill)

function Install-KillerSkills {
  param([string[]]$Skill)
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'   # the progress bar makes downloads crawl in 5.1
  if ($PSVersionTable.PSVersion.Major -lt 6) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
  }

  $repo = 'Real-Fruit-Snacks/killer-skills'
  $bundle = 'killer-skills'
  $version = if ($env:KILLER_SKILLS_VERSION) { $env:KILLER_SKILLS_VERSION } else { 'latest' }
  $base = if ($version -eq 'latest') { "https://github.com/$repo/releases/latest/download" } else { "https://github.com/$repo/releases/download/$version" }
  if ($env:KILLER_SKILLS_URL) { $base = $env:KILLER_SKILLS_URL.TrimEnd('/') }   # for tests: release files served over http
  $dest = if ($env:KILLER_SKILLS_DIR) { $env:KILLER_SKILLS_DIR } else { Join-Path (Join-Path $HOME '.claude') 'skills' }

  function Get-File([string]$Url, [string]$Path) {
    for ($try = 1; ; $try++) {
      try { Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing; return }
      catch {
        if ($try -ge 3) { throw "failed: could not download ${Url}: $($_.Exception.Message)" }
        Start-Sleep -Seconds (2 * $try)
      }
    }
  }

  $tmp = Join-Path ([IO.Path]::GetTempPath()) ('killer-skills-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $tmp | Out-Null
  $stage = $null
  try {
    $sumsFile = Join-Path $tmp 'SHA256SUMS.txt'
    Get-File "$base/SHA256SUMS.txt" $sumsFile
    $sums = @{}
    foreach ($line in Get-Content $sumsFile) {
      if ($line -match '^([0-9a-f]{64})  (.+)$') { $sums[$Matches[2]] = $Matches[1] }
    }
    $available = @($sums.Keys | Where-Object { $_ -like '*.zip' } | ForEach-Object { $_ -replace '\.zip$', '' } | Where-Object { $_ -ne $bundle } | Sort-Object)
    if (-not $available) { throw "failed: no skills listed in $base/SHA256SUMS.txt" }
    $names = @($Skill | Where-Object { $_ })
    if (-not $names) { $names = $available }
    foreach ($name in $names) {
      if ($name -cnotmatch '^[a-z0-9]+(-[a-z0-9]+)*$') { throw "failed: '$name' isn't a skill name. Available: $($available -join ' ')" }
      if ($available -cnotcontains $name) { throw "failed: no skill called $name in this release. Available: $($available -join ' ')" }
    }

    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    # Unpack beside the target, so the final step is a rename on the same drive.
    $stage = Join-Path $dest ('.killer-skills-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    $count = 0
    foreach ($name in $names) {
      $zip = Join-Path $tmp "$name.zip"
      Get-File "$base/$name.zip" $zip
      $got = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLowerInvariant()
      if ($got -ne $sums["$name.zip"]) { throw "failed: $name.zip doesn't match SHA256SUMS.txt; nothing was changed for $name" }
      $x = Join-Path $stage $name
      Expand-Archive -Path $zip -DestinationPath $x
      if (-not (Test-Path -LiteralPath (Join-Path (Join-Path $x $name) 'SKILL.md'))) { throw "failed: $name.zip has no $name/SKILL.md" }
      $target = Join-Path $dest $name
      if (Test-Path -LiteralPath $target) { $verb = 'updated'; Remove-Item -LiteralPath $target -Recurse -Force } else { $verb = 'installed' }
      Move-Item -LiteralPath (Join-Path $x $name) -Destination $target
      Write-Host "$verb $name"
      $count++
    }

    $shown = $dest
    if ($dest.StartsWith($HOME + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { $shown = '~' + $dest.Substring($HOME.Length) }
    if ($count -eq 1) { $noun = 'skill'; $them = 'it' } else { $noun = 'skills'; $them = 'them' }
    Write-Host "$count $noun in $shown"
    Write-Host "open a new Claude Code session to use $them"
  }
  finally {
    if ($stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  }
}

Install-KillerSkills -Skill $Skill
