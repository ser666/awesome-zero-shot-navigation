"""测试。

用标准库 `unittest`（而非 pytest）的原因：
    · 与本项目"零依赖"的基调一致 —— `make test` 不需要先 pip install 任何东西
    · unittest 的用例能被 pytest 直接运行 ⇒ 想要 pytest 的人也不受阻

跑法：
    python3 -m unittest discover -s tests -t . -v
    make test-service
"""
