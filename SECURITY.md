# 安全说明

这是一个**自托管的个人记账工具**：它会在你的设备上保存你的消费流水（属于敏感个人信息）。
部署和使用时请注意以下几点。

## 数据存在哪里

- 全部数据只在这个文件里：`data/moneybook.db`（SQLite）。没有云端、没有第三方服务、不联网上传。
- `data/`、`config.json`、`tmp/` 已在 `.gitignore` 里，**不要提交到任何仓库**。
- 备份 = 复制这个文件（连同 `-wal`、`-shm` 一起更稳妥）。备份本身也要当成隐私文件保管。

## 部署时的注意事项

1. **设置访问口令**。默认 `access_token` 为空，此时同一局域网内任何人都能打开你的账本。
   在 `config.json` 里填一个（或用界面的「设置」），手机首次访问用
   `http://<你的地址>:8787/login?token=你的口令` 登录一次即可。
2. **不要把端口直接映射到公网**。需要在外网用，请走 Tailscale / WireGuard / Cloudflare Tunnel 之类的
   内网穿透，并且始终开启口令。
3. 只在你信任的网络上运行。公共 WiFi 下，同网段的人可能扫到你的端口。
4. 如果用 `sync.pull_urls` 从网上拉账单，链接里不要带明文密钥。

## 推送通道里的密钥

`config.json` 的 `notify.channels` 里会填 Bark / Server 酱 / Telegram 之类的推送密钥。
这些都算密码：**不要贴到 issue、截图或群里**；一旦怀疑泄露，去对应平台重置。
本仓库里的 `config.example.json` 只有占位符，没有真实密钥。

## 通知抓取会读到什么

Android 通知监听（`android/` 或 MacroDroid 方案）只会把通知文本发给你自己的服务器，
不上传别处。但它确实能读到通知内容，所以：

- 只在你自己的手机上装
- 服务端地址填自己的内网地址或 Tailscale 地址
- 解析失败的通知会连原文一起存进 `data/moneybook.db` 的 `pending` 表，方便你补录；
  如果不想留原文，可以在界面上处理掉，或直接删掉那些记录

## 报告问题

发现安全问题时请**不要开公开 issue**，用 GitHub 的 Security → Report a vulnerability 私下告知，
或者在 issue 里只描述现象、不要贴任何流水、密钥或地址。
