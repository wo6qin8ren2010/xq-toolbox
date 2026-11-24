# 发布与自动更新（GitHub）

小庆工具箱的自动更新只需要「一个固定的版本清单地址 + 一个可匿名下载的 exe 直链」。
GitHub 公开仓库的 Release 正好满足：`releases/latest/download/<asset>` 永远指向**最新一版**的资产，
所以客户端只填一个固定前缀，之后每次发包它自己就能找到新版。

## 一、一次性准备（网页操作，约 3 分钟）

1. 登录 GitHub → 右上角 `+` → **New repository**
2. 填写：
   - Repository name：`xq-toolbox`
   - 可见性：**Public**（必须公开，否则客户端下载要带 token）
   - 不要勾选 Add README / .gitignore / license（本地已有）
3. 创建后拿到仓库地址，例如 `https://github.com/<你的用户名>/xq-toolbox`

## 二、把本地代码推上去（本机执行一次）

```powershell
cd C:\Users\Administrator\Doubao\chats\2026-11-22\new-chat\复刻_MixerTools
git remote add origin https://github.com/<你的用户名>/xq-toolbox.git
git push -u origin main
```

首次 push 会弹浏览器要求登录 GitHub，登录一次即可（之后本机记住凭据）。

> `.gitignore` 已排除 `.venv/`、`build/`、`dist/`、`*.log`、探针文件，
> 推上去的只有源码（约 2.5 MB），exe 走 Release，不进仓库历史。

## 三、以后每次发版（一条命令）

1. 改 `app/native/update.py` 的 `APP_VERSION`（例如 `"1.0.1"`）
2. 提交并打 tag：

```powershell
git add -A
git commit -m "v1.0.1: 修复 xxx"
git tag v1.0.1
git push origin main --tags
```

push tag 会触发 `.github/workflows/release.yml`：在 GitHub 的 Windows 机器上自动
安装依赖 → PyInstaller 打包 → 校验 `APP_VERSION` 与 tag 一致 → 生成 `version.json`
→ 创建 Release 并上传两个资产：

| 资产名 | 说明 |
|---|---|
| `XQToolbox.exe` | 程序本体（ASCII 名，保证直链稳定） |
| `version.json` | 版本清单（version / file / sha256 / notes） |

`APP_VERSION` 与 tag 不一致时构建会直接失败，避免客户端收到错版本。

## 四、客户端怎么填

打开「全局设置 → 软件更新」，更新源直链前缀填：

```
https://github.com/<你的用户名>/xq-toolbox/releases/latest/download
```

保存即可。之后：

- 检查更新 → 拉 `.../latest/download/version.json`，比对 `version`；
- 有新版 → 下载 `.../latest/download/XQToolbox.exe`，按清单里的 `sha256` 校验；
- 已开启静默更新 → 后台下好、退出时自动替换，下次启动就是新版。

## 五、不用 Actions 的手工兜底

不想用自动构建时，本地打包后手工发版即可：

```powershell
# 1. 本地打包得到 dist\小庆工具箱.exe
# 2. 生成清单，第三个参数是 Release 上的资产名（必须 ASCII）
.venv\Scripts\python.exe make_update_manifest.py 1.0.1 "本次更新说明" XQToolbox.exe
# 3. 把 exe 改名为 XQToolbox.exe，然后网页上 Releases → Draft a new release
#    填 tag（如 v1.0.1），上传 XQToolbox.exe 与 version.json 两个资产
```

## 常见问题

- **私有仓库能不能做自动更新？** 不能免登录。私有 Release 下载需要 token，
  把 token 塞进客户端等于把仓库写权限发给所有人，风险不值得。
  折中方案：源码放私有仓库，另建一个只装 exe 与 version.json 的公开仓库当更新源。
- **国内下载速度？** `github.com` / `objects.githubusercontent.com` 在国内时快时慢，
  介意的话可在发版后把同一份文件再同步到自己的对象存储，客户端改填那个前缀即可，
  程序逻辑完全一样。
- **旧版本会不会误判？** 现在版本按三段比较（`1.0` 与 `1.0.0` 视为同版），
  只要 tag 与 `APP_VERSION` 保持一致就不会出现「提示更新到自己」的情况。
