---
name: python-mutable-values
description: 'Guidelines and patterns for safely creating, copying, mutating, and passing mutable values in Python.'
---

# Managing Mutable Values in Python

Follow these core practices when handling mutable objects (lists, dictionaries, sets) to avoid subtle state, mutation, and reference bugs.

## 1. Avoid Modifying List Size While Iterating Over It
- Do not add or delete items from a list while looping over it directly with `for` or `while`.
- Instead, create a new list (e.g., via list comprehension) or collect changes in a secondary list and apply them after iteration.
  ```python
  # Safe: Using list comprehension
  greetings = [word for word in greetings if word == 'hello']

  # Safe: Collecting items in a separate list
  new_clothes = []
  for clothing in clothes:
      if 'sock' in clothing:
          new_clothes.append(clothing)
  clothes.extend(new_clothes)
  ```
- *Context Citation:* **Don't Add or Delete Items from a List While Looping Over It**

## 2. Copy Mutable Objects Using `copy.copy()` or `copy.deepcopy()`
- Assignment statements (`cheese = spam`) copy references, not objects.
- Use `copy.copy()` for single-level collection copies.
- Use `copy.deepcopy()` when copying collections that contain nested mutable structures (e.g., lists of lists or dicts).
  ```python
  import copy

  # Deep copy ensures nested inner structures are also duplicated
  ham = copy.deepcopy(bacon)
  ```
- *Context Citation:* **Don't Copy Mutable Values Without copy.copy() and copy.deepcopy()**

## 3. Never Use Mutable Objects for Default Arguments
- Default parameter expressions execute once when the function is defined, sharing the same mutable instance across calls.
- Set default arguments to `None` and initialize fresh mutable instances inside the function.
  ```python
  def add_ingredient(ingredient, sandwich=None):
      if sandwich is None:
          sandwich = ['bread', 'bread']
      sandwich.insert(1, ingredient)
      return sandwich
  ```
- *Context Citation:* **Don't Use Mutable Values for Default Arguments**
