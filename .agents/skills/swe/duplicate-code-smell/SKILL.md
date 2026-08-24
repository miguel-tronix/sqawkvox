---
name: duplicate-code-smell
description: 'Use when reviewing or refactoring code to detect and fix duplicate code, the most common code smell.'
---

# Duplicate Code Smell

## What it is
Duplicate code is any source code you could have created by copying and pasting some other code into your program. It is the most common code smell.

## Why it's a problem
Duplicate code makes changing code difficult: a change to one copy must be made to every copy in the program. If you forget to make a change somewhere, or make different changes to different copies, the program will likely end up with bugs.

## How to fix it
Deduplicate: make the code appear once in the program by placing it in a function or loop. You can also combine both techniques — parameterize the parts that differ and pass them to a function called from a loop.

Example — original duplicate code:

```python
print('Good morning!')
print('How are you feeling?')
feeling = input()
print('I am happy to hear that you are feeling ' + feeling + '.')
print('Good afternoon!')
print('How are you feeling?')
feeling = input()
print('I am happy to hear that you are feeling ' + feeling + '.')
print('Good evening!')
print('How are you feeling?')
feeling = input()
print('I am happy to hear that you are feeling ' + feeling + '.')
```

Deduplicated with a loop:

```python
for timeOfDay in ['morning', 'afternoon', 'evening']:
    print('Good ' + timeOfDay + '!')
    print('How are you feeling?')
    feeling = input()
    print('I am happy to hear that you are feeling ' + feeling + '.')
```

Deduplicated with a function and a loop (parameterizing the differing part):

```python
def askFeeling(timeOfDay):
    print('Good ' + timeOfDay + '!')
    print('How are you feeling?')
    feeling = input()
    print('I am happy to hear that you are feeling ' + feeling + '.')

for timeOfDay in ['morning', 'afternoon', 'evening']:
    askFeeling(timeOfDay)
```

## Judgment call — when NOT to deduplicate
Avoiding duplicate code is not a hard-and-fast rule. General guidance:

- The longer the duplicate section, or the more duplicate copies, the stronger the case for deduplication.
- A common heuristic: start considering deduplication when 3–4 copies exist in the program; 1–2 copies may be fine.
- Sometimes code is simply not worth the trouble of deduplicating — a simple, straightforward block may be clearer than a loop/function/parameter structure that adds complexity.

Always weigh the added indirection (loop variables, functions, parameters) against the risk of inconsistent edits.

