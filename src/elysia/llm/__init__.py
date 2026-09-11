"""P2 表达生成子包：LLM 调用抽象、输出校验器、微声模板。

分工：
- validator.py: 输出校验器（T2 防线）
- micro.py: 微声模板（无 LLM 时的结构化呓语）
- chain.py: 三级降级调度器（主声→次声→微声）
- words.py 位于 soul 层，供校验器消费词汇表
"""

__all__ = ["chain", "micro", "validator"]
