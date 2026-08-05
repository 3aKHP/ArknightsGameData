# ArknightsGameData

《明日方舟》游戏数据兼容分发仓库。

本仓库不再直接同步 Kengxxiao/ArknightsGameData 的 git 历史，而是从
[arknights-data-pipeline](https://github.com/3aKHP/arknights-data-pipeline)
（AKDP）工厂仓库的 GitHub Release 投影 LTS 所需的 zh_CN 资产。

## 资产

每个 Release 包含：

| 资产 | 说明 |
|------|------|
| `zh_CN-excel.zip` | `zh_CN/gamedata/excel/` 数据表 |
| `zh_CN-levels.zip` | `zh_CN/gamedata/levels/` 关卡数据 |
| `manifest.json` | 溯源、指标和 SHA-256 校验 |

`zh_CN-resource-manifest.zip` 是未被 AKDP 工厂化的 legacy 可选资产，当前
不纳入兼容 Release。如果未来 LTS 或受支持消费者需要该资产，应先在 AKDP
工厂仓库建立其生产契约。

## 发布流程

`.github/workflows/sync-and-release.yml` 定时检查 AKDP 最新非 draft Release：

1. 下载工厂 `manifest.json`、`zh_CN-excel.zip`、`zh_CN-levels.zip`
2. 通过 `scripts/akdp_source.py` 验证字节 SHA-256/size 与工厂清单一致
3. 运行 `scripts/release_gate.py` 做契约、回归和完整性校验
4. 以 `akdp-<versionId>-v1` 标签创建 draft Release，回下校验后公开

兼容仓只投影原始字节，不重新压缩、不注入数据、不调用 LLM。

## 测试

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py
```
