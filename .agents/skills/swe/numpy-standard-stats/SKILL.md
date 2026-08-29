---
name: numpy-standard-stats
description: 'Compute standard summary statistics on numerical arrays using NumPy functions.'
---

# NumPy Standard Statistics

Use NumPy statistical functions to calculate summary statistics for 1D or multi-dimensional numerical arrays.

## Core Statistical Functions

- **Mean (`np.mean`)**: Calculates the arithmetic mean of array elements.
- **Median (`np.median`)**: Calculates the middle value of sorted array elements (useful for skewed data/outliers).
- **Standard Deviation (`np.std`)**: Measures the amount of variation or dispersion of data from the mean.
- **Variance (`np.var`)**: Calculates the average squared differences from the mean.
- **Minimum (`np.min`) & Maximum (`np.max`)**: Retrieves the smallest and largest values to assess data range and detect extreme values.

## Usage Snippet (1D Array)

```python
import numpy as np

heights = np.array([150, 160, 170, 180, 190])

mean_h = np.mean(heights)      # 170.0
median_h = np.median(heights)  # 170.0
std_h = np.std(heights)        # ~14.14
var_h = np.var(heights)        # 200.0
min_h = np.min(heights)        # 150
max_h = np.max(heights)        # 190
```

## Aggregating Along Axes (2D Arrays)

Pass the `axis` parameter to aggregate across rows or columns:
- `axis=0`: Computes statistics down each column across rows.
- `axis=1`: Computes statistics across each row across columns.

```python
arr_2d = np.array([
    [150, 50],
    [160, 60],
    [170, 65],
    [180, 80],
    [190, 90]
])

# Column-wise means [mean_height, mean_weight]
col_means = np.mean(arr_2d, axis=0)  # array([170.,  69.])
```
