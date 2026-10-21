"""Junior-school (CBC) grading, taken from the school's exam workbook "JUNIOR SCHOOL EXAMS TERM 2 2026".

Workbook formula                               ->  here
  MEAN   =AVERAGE(exam1:exam3)                 ->  mean()      blank exams are ignored, exactly as Excel's AVERAGE does
  POINTS =VLOOKUP(mean, bands, 3, TRUE)        ->  band(mean)[2]
  GD1-3, MG =VLOOKUP(x, bands, 2, TRUE)        ->  band(x)[1]  (level of each exam, and of the mean)
  TOTAL  =SUM(subject means)                   ->  student_total()
  TOTAL POINTS =SUM(subject points)            ->  student_total()
  POSITION =RANK(total, class totals, 0)       ->  rank()      ties share a position and the next one is skipped (1, 2, 2, 4)

Bands are an approximate match (TRUE): a value takes the highest threshold that is <= the value, so 57.9 is ME2 and 58 is ME1.
"""
BANDS = [(0, 'BE2', 1), (11, 'BE1', 2), (21, 'AE2', 3), (31, 'AE1', 4), (41, 'ME2', 5), (58, 'ME1', 6), (75, 'EE2', 7), (90, 'EE1', 8)]
LEVEL_NAMES = {'EE1': 'Exceeding Expectations 1', 'EE2': 'Exceeding Expectations 2', 'ME1': 'Meeting Expectations 1', 'ME2': 'Meeting Expectations 2',
               'AE1': 'Approaching Expectations 1', 'AE2': 'Approaching Expectations 2', 'BE1': 'Below Expectations 1', 'BE2': 'Below Expectations 2'}

def band(x):
    """(threshold, level, points) for a mark or mean between 0 and 100."""
    hit = BANDS[0]
    for b in BANDS:
        if x >= b[0]: hit = b
    return hit

def mean(values):
    v = [float(x) for x in values if x is not None]
    return sum(v) / len(v) if v else None

def subject_summary(exams):
    """exams = [exam1, exam2, exam3] (None where not sat). Returns the per-subject columns of the workbook."""
    exams = [None if x is None else float(x) for x in exams]
    m = mean(exams)
    return {'exams': exams, 'exam_levels': [band(x)[1] if x is not None else None for x in exams],
            'mean': m, 'level': band(m)[1] if m is not None else None, 'points': band(m)[2] if m is not None else None}

def student_total(summaries):
    means = [s['mean'] for s in summaries if s['mean'] is not None]
    return {'total': sum(means) if means else None, 'total_points': sum(s['points'] for s in summaries if s['points'] is not None) if means else None}

def rank(totals):
    """totals = {id: total or None}. Same as Excel RANK(...,0): 1 + the number of students with a higher total."""
    scored = {k: v for k, v in totals.items() if v is not None}
    return {k: 1 + sum(1 for o in scored.values() if o > v) for k, v in scored.items()}
