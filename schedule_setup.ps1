# 서버 예약 실행 등록 (한 번만 실행하면 됨)
#   콜라보 수집기  : 매일 06:30
#   블로그 자동화  : 매일 03:00(새벽) / 12:40 / 19:40  (이미 돌고 있으면 그 회차는 건너뜀)
# 실행 방법: PowerShell 에서  powershell -ExecutionPolicy Bypass -File C:\blog\schedule_setup.ps1

$pyw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive

# 콜라보 수집기
$a1 = New-ScheduledTaskAction -Execute $pyw -Argument '"C:\blog\collabo_daily.pyw"' -WorkingDirectory 'C:\blog'
$t1 = New-ScheduledTaskTrigger -Daily -At '06:30'
$s1 = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -WakeToRun -StartWhenAvailable
Register-ScheduledTask -TaskName 'AUTO_collabo_daily' -Action $a1 -Trigger $t1 -Settings $s1 -Principal $principal -Force | Out-Null

# 블로그 자동화 (새벽/점심/저녁)
$a2 = New-ScheduledTaskAction -Execute 'C:\blog\blog_auto_run.bat' -WorkingDirectory 'C:\blog'
$t2 = @(
  (New-ScheduledTaskTrigger -Daily -At '03:00'),
  (New-ScheduledTaskTrigger -Daily -At '12:40'),
  (New-ScheduledTaskTrigger -Daily -At '19:40')
)
$s2 = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -WakeToRun -StartWhenAvailable
Register-ScheduledTask -TaskName 'AUTO_blog' -Action $a2 -Trigger $t2 -Settings $s2 -Principal $principal -Force | Out-Null

Get-ScheduledTask -TaskName 'AUTO_*' | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime | Format-Table -AutoSize
Write-Host '예약 등록 완료!'
