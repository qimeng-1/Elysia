"""P2 表达生成子包：LLM 调用抽象、输出校验器、微声模板。

分工：
- validator.py: 输出校验器（T2 防线）
- words.py 位于 soul 层，供校验器消费词汇表
- chain.py/backends: 双轨降级链（后续步骤）
"""

__all__ = ["validator"]
