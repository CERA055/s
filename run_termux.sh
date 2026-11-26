#!/data/data/com.termux/files/usr/bin/bash
# Termux 后台守护。装一次，开机自启，之后不用管。
#
#   pkg install python termux-api cronie
#   pip install requests
#   mkdir -p ~/soliumbra && cd ~/soliumbra   # 放 soliumbra_claim.py 和 cookie.txt
#   mkdir -p ~/.termux/boot
#   cp run_termux.sh ~/.termux/boot/claim.sh && chmod +x ~/.termux/boot/claim.sh
#
# 还需要（否则 Android 的 Doze 一定会把它杀掉）：
#   1) 装 Termux:Boot 插件并打开一次
#   2) 系统设置 → 电池 → Termux → 不限制 / 允许后台活动
#   3) termux-wake-lock（本脚本已自带）

set -u
cd "$(dirname "$0")/.." 2>/dev/null || true
[ -f soliumbra_claim.py ] || cd "$HOME/soliumbra" || exit 1

NOTIFY="--notify --quiet-ok"
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] || NOTIFY=""

termux-wake-lock 2>/dev/null || true
echo "[$(date '+%F %T')] 守护启动 pid=$$" >> claim.log

# 用轮询而不是 crond：crond 在 sleep/Doze 下基本不触发，唤醒后才补跑，
# 而 while+sleep 只要 wakelock 在就能按时回到这个循环。
while true; do
  H=$(date +%H)
  M=$(date +%M)

  if [ "$M" = "05" ]; then
    if [ "$H" = "00" ]; then
      # 零点这一轮把 daily 一起做掉
      python soliumbra_claim.py --all $NOTIFY >> claim.log 2>&1
    else
      # 整点后 5 分领补给（会被 turnstile 拦就靠打码平台）
      python soliumbra_claim.py --kind hourly $NOTIFY >> claim.log 2>&1
    fi
    # 跳过本分钟剩余时间，避免同一分钟重复触发
    sleep 50
  else
    sleep 20
  fi
done
