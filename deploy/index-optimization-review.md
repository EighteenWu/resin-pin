# pan-so 搜索索引优化评估（2026-10-14）

## 当前事实

- 生产 Meilisearch 为 1.28.2，只有 `resources` 一个索引，约 14,519,811 个文档，无向量索引、无废弃重复索引。
- 实例占用约 60.7 GiB，有效页面约 49.1 GiB；`databaseSize - usedDatabaseSize` 约 11.6 GiB。这是实例级估计，包含约 0.47 GiB 的任务库，不承诺单次资源索引压缩能释放全部差值。
- 原始文档存储约 14.5 GiB。全文字段已限制为 `title`、`text_content`、`author`，没有把所有 25 个文档字段都建立全文索引。
- 过滤字段包括网盘类型、文件类型、有效性、作者、是否本人资源及创建/更新时间；排序字段包括访问次数、标题及创建/更新时间。时间字符串和时间戳存在两套字段，是否能去掉一套需要查明全部调用方。
- 日志清理前索引所在根文件系统剩余约 6.2 GiB；即使清出约 3 GiB，也不足以执行原地压缩。

## 首选方案：仅压缩现有索引

先让索引所在文件系统有至少约 70 GiB 可用空间，再在低峰执行一次官方 `POST /indexes/resources/compact`，跟踪返回的 taskUid。70 GiB 是按约 60 GiB 索引与额外余量制定的保守操作门槛，届时要重新测量。

Meilisearch 1.28.2 的实现将压缩副本 `data.mdb.cpy` 写在原索引目录内。因此只给 `/tmp` 挂一块临时磁盘并不能解决空间不足；需要扩容索引所在文件系统，或者完整迁移 Meilisearch 数据目录到有足够空间的磁盘。迁移是另一份需确认的部署方案，本轮不执行。

本方案不删除文档，不改搜索、过滤、排序和相关性设置，不要求重建业务数据库。对业务的影响是：

- 官方设计在复制压缩期间继续提供搜索；磁盘 I/O 和 CPU 会增加，搜索延迟可能上升。
- 压缩占据 Meilisearch 任务队列，后续新增、更新、删除文档及设置任务排队。因此已入库的新资源可能暂时晚些出现在搜索结果中。
- 1.28.2 在完成副本替换后关闭并重新打开该索引；不能把“可继续搜索”解释为搜索延迟毫无波动。
- 全部操作只作用于派生搜索索引。PostgreSQL 中的资源、用户、任务及原有备份不改变。

操作前检查 processing/enqueued 队列、索引健康和实际空闲空间，准备可恢复的索引状态或外部容量；不在目前 96% 占用的根盘生成大型 dump/snapshot。仅在任务成功后验收，不仅检查 POST 是否返回 202。

验收比对文档数量、字段设置、典型关键词结果与顺序、网盘/有效性过滤、排序、翻页、搜索延迟、增量同步恢复和磁盘实际释放量。物理碎片整理没有设置变更，可保持原有搜索功能；实际空间与速度收益以生产验收为准。

## 后续结构优化

在隔离环境审计并验证冗余时间字段和不再使用的过滤/排序字段，再决定是否精简。更改 searchable/filterable/sortable attributes 会重新索引，可能使旧调用报错、搜索结果变化或排队时间增加，不属于无感物理压缩。

不直接关闭标题/正文搜索、前缀匹配、拼写容错或修改相关性规则来压缩空间。大规模设置调整与全量重建必须使用独立容量、样例回归和明确维护计划；不会加入自动日志维护计时器。

## 依据

- [官方压缩指南：临时空间、搜索可用性与任务队列](https://www.meilisearch.com/docs/capabilities/indexing/how_to/compact_an_index)
- [官方 Compact API](https://www.meilisearch.com/docs/reference/api/indexes/compact-index)
- [实际版本 1.28.2 的压缩实现](https://github.com/meilisearch/meilisearch/blob/v1.28.2/crates/index-scheduler/src/scheduler/process_batch.rs#L545)
- [实际版本 Resin 的日志库发现、读取及轮转机制](https://github.com/Resinat/Resin/blob/42dff8c0e4e96b465b5bd94f75a484c34e3cd658/internal/requestlog/repo.go)
