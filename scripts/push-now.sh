#!/usr/bin/env bash
# Mochi 準備好的一次性 push 指令
# 在你電腦的 Terminal.app 跑這一行就好（複製貼上即可）
#
# 跑完會：
#   1. 把 2 個本地 commit（wave.html + push-local-commits.yml）推到 origin/main
#   2. macOS 跳出 GitHub 登入窗，你按同意（會記進 Keychain，下次不用再登）
#   3. 完成 → GH Pages 幾分鐘內生效 → https://xbaoamigo-stack.github.io/tw-stock-advisor/wave.html
#
# 安全保證：
#   - 不需要 PAT（GitHub 登入用瀏覽器 OAuth，PAT 完全不過本機）
#   - 不會把任何 token 寫到 disk
#   - 不會出現在 Telegram 對話紀錄

cd ~/.openclaw/workspace/tw-stock-advisor && git push origin main
