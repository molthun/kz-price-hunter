from tests import test_support
import local_search,json
categories=[{'id':'tires','name':'Шины и диски','fields':{'season':{'label':'Сезон'},'studs':{'label':'Шипы'},'width':{},'profile':{},'diameter':{}}}]
q='зимняя резина 205/55 R16'
answer={'category_id':'tires','search_query':'зимн 205/55 R16','attributes':{'season':'Зимние','studs':'Да','width':'205','profile':'55','diameter':'16'},'confidence':0.99}
p=local_search.clean_plan(answer,q,categories)
print(json.dumps({'query':q,'accepted_plan':p},ensure_ascii=False))
