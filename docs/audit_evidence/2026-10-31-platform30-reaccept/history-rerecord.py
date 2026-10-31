import contextlib,io,json,runpy
from pathlib import Path
from domain.models import OfferPriceHistory
with contextlib.redirect_stdout(io.StringIO()):
    state=runpy.run_path(str(Path(__file__).with_name('independent.py')))
c=state['conn'];offers=state['offers'];offer=state['offer'];history=state['history']
rows={}
for writer in ('single','batch','model'):
    c.execute('DELETE FROM offer_price_history')
    offers.save_or_update(offer())
    def record(price):
        data=dict(offer_id='offer',price=price,observed_at='2026-10-30T10:00:00Z')
        if writer=='single':history.record_point(**data)
        elif writer=='batch':history.record_batch([data])
        else:history.record_history_model(OfferPriceHistory(**data))
    record(100)
    offers.save_or_update(offer(pid='p2',cid='kaspi',condition='USED',price=200))
    record(105)
    row=dict(c.execute('SELECT * FROM offer_price_history').fetchone())
    assert (row['product_id'],row['seller_id'],row['channel_id'],row['condition'])==('p1','s1','direct','NEW'),row
    model=history.get_offer_price_history('offer')[0]
    assert (model.product_id,model.channel_id,model.condition)==('p1','direct','NEW')
    rows[writer]=row
print(json.dumps(rows,ensure_ascii=False,indent=2))
