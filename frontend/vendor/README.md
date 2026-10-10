# 自托管前端依赖

`backend/app/templates/dashboard.html` 直接引用本目录文件，页面**不再依赖公网 CDN**。这样做的原因：

- 本机 DNS 被代理接管（`cdn.jsdelivr.net` 解析成 `198.18.0.x` 的 fake-IP），CDN 请求要绕经代理 TUN，首屏会多出数秒等待；
- 隔离网/断网环境下页面仍能正常渲染与交互。

| 文件 | 版本 | 上游来源 | 许可证 |
|---|---|---|---|
| `bootstrap-5.3.3.min.css` | 5.3.3 | `https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css` | MIT |
| `htmx-2.0.4.min.js` | 2.0.4 | `https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js` | BSD-2-Clause |
| `alpinejs-3.14.8.min.js` | 3.14.8 | `https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js` | MIT |
| `echarts-5.5.1.min.js` | 5.5.1 | `https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js` | Apache-2.0 |

文件名内嵌版本号，因此可以长期缓存；升级时**新增**带新版本号的文件并同步修改模板引用，不要就地覆盖旧文件。

下载/更新命令（在仓库根目录，使用项目解释器）：

```powershell
python - <<'PY'
import pathlib, httpx
targets = {
    "bootstrap-5.3.3.min.css": "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css",
    "htmx-2.0.4.min.js": "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js",
    "alpinejs-3.14.8.min.js": "https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js",
    "echarts-5.5.1.min.js": "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js",
}
out = pathlib.Path("frontend/vendor")
for name, url in targets.items():
    content = httpx.get(url, timeout=60, follow_redirects=True).raise_for_status().content
    (out / name).write_bytes(content)
    print(name, len(content))
PY
```
