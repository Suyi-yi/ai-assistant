# 发布包放这里

这个目录用来放给朋友下载的安装包和更新清单，程序内置的「检查更新」就是从这里的
`manifest.json` 读版本的。

## 每次发新版怎么做

1. 在本机跑 `build.bat` 打包，再跑：

   ```powershell
   powershell -ExecutionPolicy Bypass -File package_release.ps1
   ```

2. 把 `release\AI小助理-vX.Y.Z.zip` 复制到这个目录，旧版本包可以删掉（只留最新一个，
   不然仓库会越来越大）。

3. 编辑这个目录里的 `manifest.json`，三件事：
   - `version` 改成新版本号，要和 `main.py` 里的 `VERSION` 一致
   - `url` 改成这个 zip 的**原始文件地址**（在网页上点开该文件，点「原始数据 / Raw」，
     复制地址栏那一串）
   - `notes` 写这一版改了什么，朋友更新时会看到

4. 提交并推送：

   ```powershell
   git add releases
   git commit -m "发布 vX.Y.Z"
   git push
   ```

5. 回到程序设置里，把「更新源」填成 `manifest.json` 的原始文件地址，点「立即检查」
   验证一下能不能读到新版本。

## 注意

- 仓库必须是**公开**的，否则朋友的程序下载不了。
- 不要把这个目录之外的 `release\` 文件夹提交上去，那是本机构建产物。
