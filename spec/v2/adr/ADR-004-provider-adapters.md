# ADR-004: Provider adapter 境界

- Status: accepted
- Decision: Core は provider-neutral protocol のみを扱い、SDK response は adapter 内で正規化する。
- Reason: Provider 喪失・SDK変更・response decode 差分から Core を守る。
- Consequence: adapter ごとに contract probe と capability の観測結果を持つ。
