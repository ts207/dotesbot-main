with open('tests/test_strategy_collection.py', 'a') as f:
    f.write('''
def test_dswing_markout_logger_receives_dswing_signal_fields(base_ctx):
    base_ctx.dswing_enabled = True
    base_ctx.enable_match_winner_trading = True
    base_ctx.dswing_engine = MagicMock()
    base_ctx.loggers.markout_logger_fn = MagicMock()
    
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = "tok_yes"
    sig.direction = "radiant"
    sig.edge = 0.05
    sig.series_fair = 0.87
    sig.game_time_sec = 900
    sig.edge_type = "dswing"
    sig.target_horizon = "end"
    sig.expected_hold_sec = 600
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    sig.ask = 0.82
    sig.side = "YES"

    base_ctx.dswing_engine.evaluate.return_value = [sig]
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 1
    base_ctx.loggers.markout_logger_fn.assert_called_once()
    called_args = base_ctx.loggers.markout_logger_fn.call_args[0]
    markout_row = called_args[0]
    assert markout_row["event_type"] == "DSWING"
    assert markout_row["executable_edge"] == 0.05
    assert markout_row["edge_type"] == "dswing"

def test_match_winner_research_enabled_does_not_execute_when_trading_disabled(base_ctx):
    base_ctx.dswing_enabled = True
    base_ctx.enable_match_winner_trading = False
    base_ctx.mapping["market_type"] = "MATCH_WINNER"
    base_ctx.dswing_engine = MagicMock()
    base_ctx.loggers.markout_logger_fn = MagicMock()
    
    sig = MagicMock(spec=DSwingSignal)
    sig.token_id = "tok_yes"
    sig.direction = "radiant"
    sig.edge = 0.05
    sig.series_fair = 0.87
    sig.game_time_sec = 900
    sig.edge_type = "dswing"
    sig.target_horizon = "end"
    sig.expected_hold_sec = 600
    sig.entry_trigger = "e"
    sig.exit_trigger = "x"
    sig.primary_metric = "p"
    sig.secondary_metric = "s"
    sig.promotion_rule = "pr"
    sig.disable_rule = "dr"
    sig.ask = 0.82
    sig.side = "YES"

    base_ctx.dswing_engine.evaluate.return_value = [sig]
    candidates = collect_strategy_candidates(base_ctx)
    
    assert len(candidates) == 0
    base_ctx.loggers.markout_logger_fn.assert_called_once()
''')
