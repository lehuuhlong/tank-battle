#!/usr/bin/env python3
"""In danh sách chiến thuật, mô tả và tham số."""
import strategies

for name, cls in sorted(strategies.load_all().items()):
    print(f"■ {name}\n  {cls.summary}")
    for k, v in cls.params_help.items():
        print(f"    --param {k}=…  {v}")
    print()
