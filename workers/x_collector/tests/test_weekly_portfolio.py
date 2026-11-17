from workers.x_collector.portfolio_rules import replacement_plan, weekly_scores


def rows():
    return [{'query': 'پوست خشک', 'domain': 'skin', 'runs': 10, 'leads': 20},
            {'query': 'کرم صورت', 'domain': 'skin', 'runs': 10, 'leads': 0},
            {'query': 'کرم خوب', 'domain': 'skin', 'runs': 1, 'leads': 0}]


def test_new_query_starts_at_search_weighted_tenant_mean():
    assert weekly_scores(rows())['کرم خوب'] == 0
    data = rows()[:2] + [{'query': 'پوست صورتم', 'runs': 0, 'leads': 0}]
    assert weekly_scores(data)['پوست صورتم'] == 1


def test_replacement_needs_3000_reviewed_posts_for_this_tenant():
    assert replacement_plan(rows(), 2999, [{'query': 'پوست صورتم', 'domain': 'skin'}]) == []


def test_replace_only_tested_loser_and_preserve_exact_size():
    assert replacement_plan(rows(), 3000, [{'query': 'پوست صورتم', 'domain': 'skin'}]) == [
        ('کرم صورت', {'query': 'پوست صورتم', 'domain': 'skin'})]


def test_no_deletion_without_a_valid_replacement_in_same_family():
    assert replacement_plan(rows(), 3000, []) == []
    assert replacement_plan(rows(), 3000, [{'query': 'کلاس کنکور', 'domain': 'education'}]) == []


def test_required_names_and_new_queries_are_not_removed():
    data = rows()
    data[1]['required'] = True
    assert replacement_plan(data, 3000, [{'query': 'پوست صورتم', 'domain': 'skin'}]) == []


def test_all_zero_yields_still_allow_a_tested_query_to_be_replaced():
    data = [{'query': 'a', 'domain': 'skin', 'runs': 10, 'leads': 0},
            {'query': 'b', 'domain': 'skin', 'runs': 10, 'leads': 0}]
    assert len(replacement_plan(data, 3000, [{'query': 'new', 'domain': 'skin'}])) == 1


def test_partial_replacements_are_never_applied():
    data = [{'query': str(i), 'domain': 'skin', 'runs': 10, 'leads': i} for i in range(10)]
    assert replacement_plan(data, 3000, [{'query': 'one', 'domain': 'skin'}]) == []
    assert len(replacement_plan(data, 3000, [{'query': q, 'domain': 'skin'} for q in ['one', 'two']])) == 2


def test_best_queries_are_compared_across_all_children_of_the_tenant():
    data = [{'query': 'a', 'domain': 'first-child', 'runs': 10, 'leads': 20},
            {'query': 'b', 'domain': 'second-child', 'runs': 10, 'leads': 1}]
    assert replacement_plan(data, 3000, [{'query': 'new', 'domain': 'second-child'}]) == [
        ('b', {'query': 'new', 'domain': 'second-child'})]


def test_candidates_must_be_short_relevant_and_never_previously_used():
    from workers.x_collector.families import Family, family_topic_contexts
    from workers.x_collector.portfolio import _prepare_candidates
    family = Family('skin', '1', 'کرم پوست', 'زیبایی > پوست', '', ['1'])
    contexts = family_topic_contexts([family])
    proposals = [{'domain': 'skin', 'query': q} for q in
                 ['کلاس کنکور', 'پوست صورت خیلی خشک', 'کرم پوست', 'خشکی پوست', 'خشکی پوست']]
    assert _prepare_candidates(proposals, {'skin': family}, contexts, ['"کرم پوست" lang:fa']) == [
        {'query': '"خشکی پوست" lang:fa', 'domain': 'skin', 'kind': 'problem'}]
