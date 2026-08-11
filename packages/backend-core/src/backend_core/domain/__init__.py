"""Domain layer — entities, enums and state machines.

The rules that stay true regardless of how data is stored or exposed: the
Product (§104), Project (§105) and Job (§106) state machines, the role model
(§40), and the verification states that make the Truth Layer work (§13).

State transitions are validated here, never by assigning a string to a status
column — §105 is explicit that arbitrary status writes are forbidden.

Populated from PHASE 3 as each entity arrives.
"""
