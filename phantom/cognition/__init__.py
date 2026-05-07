"""
phantom.cognition — embodied-agency scaffolding on top of PhantomMemory.

This is the "reflective body" layer. LM Studio remains the mind: it
plans, reasons, writes language. Phantom is the body: it perceives,
acts, remembers, and now also offers itself a structured way to ask
"is what I just wrote / am about to do actually right?".

Nothing here runs autonomously. Every cognition tool is invoked by the
LM Studio model exactly like any other tool. The point is to give the
model cheap, deterministic scaffolding so it does not have to reinvent
goal/plan/reflect/risk-check on its own every turn.
"""
from phantom.cognition.core import AgentCognition

__all__ = ["AgentCognition"]
