# v0.4 Cloudflare 部署说明

本版无需购买域名即可先部署到 Workers 提供的地址。域名可以在阿里云购买，续费继续由阿里云管理，再将 DNS 接入 Cloudflare。没有内网穿透。以下命令只是说明，首版交付时尚未执行远端资源创建或发布。

## 代码与环境

`cloud/src/worker.js` 同时用于本机适配器与 Cloudflare Workers；`cloud/public` 是产品网站；`cloud/migrations` 是 D1 表结构。开发需要 Node.js 24、Python 3.13 和原项目虚拟环境。使用 Windows 打包助手的最终用户无需安装这些开发环境。

先运行测试：

```powershell
node --test cloud/test/*.test.mjs
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts/check_ui_v4.py
```

## 当前部署状态（2026-09-14）

正式网站 `https://bnbucoursenest.cn` 已部署，D1 数据库 `coursenest` 已初始化，运行时 `INVITE_CODE` Secret 已配置，真实助手已配对并持续心跳。数据库 ID 为 `743d0018-d56f-4e10-8d87-666613847f84`，不是访问密钥。

Workers Builds 使用项目名 `bnbu-coursenest`、根目录 `cloud`、生产分支 `codex/v0.4-coursenest`，构建命令留空，部署命令 `npx wrangler deploy`。v0.4 发布后 main 同步到正式版本；目前 Cloudflare 自动部署仍跟踪原分支，避免中断既有配置。

生产域名已在 Worker 绑定，`PUBLIC_ORIGIN=https://bnbucoursenest.cn`。邀请码保存在运行时 Secret，不放在构建变量或 Git 中。临时 workers.dev 地址不在允许范围。

已验证公网健康接口、真实电脑配对与心跳；跨账号隔离、选择同步、去重等由模拟平台测试覆盖，多台物理电脑公网验收尚未执行。

## Wrangler 部署步骤

在 `cloud` 目录，使用 Cloudflare 官方 Wrangler 登录并创建 D1：

```powershell
npx wrangler login
# 仅首次创建数据库时执行；当前账号已经创建，不要重复创建。
# npx wrangler d1 create coursenest
```

把返回的数据库 ID 填到 `wrangler.jsonc` 的 `database_id`。把 `PUBLIC_ORIGIN` 改为最终完整 HTTPS 网站地址，例如实际分配的 `https://bnbu-coursenest.<你的子域>.workers.dev`，不能照抄占位地址，也不能保留 `LOCAL_DEV=1`。

设置一个新的私密邀请码，在交互提示中输入，不写入 Git 或命令历史：

```powershell
npx wrangler secret put INVITE_CODE
npx wrangler d1 migrations apply coursenest --remote
npx wrangler deploy
```

部署后先检查 `/api/health` 的 `local` 必须为 false；浏览器验证 HTTPS Cookie、注册登录、跨站拒绝、配对、D1 任务领取和 Cron，再进行两账号、两台真实电脑隔离与下载验收。本机 Node 测试无法替代 Workers 运行时和 D1 真机验证。

本地开发账号和数据库不自动迁移到云端。正式网站首次注册后重新生成配对码；助手先断开旧本机网站，再填写公网 HTTPS 地址配对。学校账号与授权目录不需要迁移到云端。

## 阿里云域名接入

域名购买成功后，将它添加到 Cloudflare，按 Cloudflare 分配的两个名称服务器在阿里云修改 DNS 服务器。生效后，在 Worker 的 Domains & Routes 添加自定义域名，并同时更新 `PUBLIC_ORIGIN` 后重新部署。修改域名后，助手中的网站地址也需更新配对。

注册域名不会自动部署代码。先完成部署与验收，再配置域名，可避免支付和 DNS 问题阻塞开发。是否需要付费套餐以当时 Cloudflare 配额、实际 CPU/请求/D1 用量为准；本版没有自动开通付费计划。

官方参考：[Workers 静态资源](https://developers.cloudflare.com/workers/static-assets/)、[D1 迁移](https://developers.cloudflare.com/d1/reference/migrations/)、[Worker 自定义域名](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/)。
