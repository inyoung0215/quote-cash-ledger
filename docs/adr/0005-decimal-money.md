# ADR-0005. 금액은 Decimal 로만 다룬다

## 결정

- Python: `decimal.Decimal`
- MySQL: `NUMERIC(18, 2)`
- 입력은 **반드시 문자열을 거쳐** Decimal 로 만든다

```python
Decimal(0.1) != Decimal("0.1")   # 앞은 이미 오차가 들어간 값
```

## 이유

float 은 2진 부동소수점이라 0.1 같은 10진 소수를 정확히 표현하지 못한다.
한 건에서는 무시할 수준이지만 정산은 합산이라 오차가 누적되고, 그러면 대사가 맞지 않는다.

`Decimal(0.1)` 함정은 Java의 `new BigDecimal(0.1)` 과 완전히 같다.
그래서 Pydantic 스키마에서 `mode="before"` 검증기로 한 번 감싸
어떤 입력이 오든 `Decimal(str(v))` 를 거치게 했다.

## 검증

`tests/test_money_precision.py` 에서 0.01 짜리 300건을 확정해
정확히 3.00 이 빠지는지 확인한다. float 이었다면 여기서 어긋난다.

## 참고: Java 와 다른 점

- Java 는 금액 비교에 `equals` 대신 `compareTo` 를 써야 한다 (소수 자릿수까지 비교하므로).
  Python `Decimal` 의 `==` 는 값으로 비교하므로 이 함정은 없다.
- 반올림은 양쪽 모두 **명시해야 한다**. `quantize(Decimal('0.01'), rounding=ROUND_DOWN)`.
  절사 방향이 곧 정책이다. 이 레포는 원 단위라 반올림이 개입하지 않지만,
  수수료가 붙는 순간 명시가 필요하다.
