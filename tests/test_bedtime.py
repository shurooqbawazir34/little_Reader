"""Bedtime API checks; AI calls are mocked, so tests use no paid requests."""
import asyncio
from unittest.mock import patch
from tests.test_api import client, test_health, test_auth_and_parent_isolation
import backend.main as backend

def test_bedtime():
    token=client.post('/api/auth/register',json={'email':'bedtime@example.com','password':'secure-secret-123'}).json()['token']
    h={'Authorization':'Bearer '+token}
    c=client.post('/api/children',headers=h,json={'name':'سارة','age':5}).json()
    cid=c['id'];assert c['reading_level']==2
    url='/api/bedtime/'+cid
    assert client.post(url+'/story',headers=h,json={'answers':['فرحت','تعبت','ساعدت أمي']}).status_code==400
    settings={'consent':True,'gender':'girl','bedtime':'20:30','language':'ar','recipient':'الجدة'}
    assert client.put(url,headers=h,json={**settings,'consent':False}).status_code==400
    assert client.put(url,headers=h,json=settings).status_code==200
    assert client.get(url,headers=h).json()['bedtime']=='20:30'
    other=client.post('/api/auth/register',json={'email':'other-bedtime@example.com','password':'secure-secret-123'}).json()['token']
    assert client.get(url,headers={'Authorization':'Bearer '+other}).status_code==404
    calls=[]
    async def unsafe(*args):calls.append(args);return {'safe':False}
    with patch.object(backend,'openai_json',unsafe):
        assert client.post(url+'/story',headers=h,json={'answers':['أ','ب','ج']}).status_code==422
    assert len(calls)==1
    assert client.get('/api/stories',headers=h).json()==[]
    result={'title':'حكايتي','pages':[{'text':'يوم جميل','child_line':'أنا سعيد','scene':'garden'} for _ in range(5)],'words':['يوم','جميل','سعيد'],'task':'قل شكراً','parent_question':'ما الذي أسعدك؟','feelings':['فرح','تعب','لطف']}
    async def generate(system,prompt):return {'safe':True} if 'assess child safety' in system else result
    with patch.object(backend,'openai_json',generate):
        response=client.post(url+'/story',headers=h,json={'answers':['أ','ب','ج']})
    assert response.status_code==200,response.text
    s=response.json();assert len(s['pages'])==5 and s['safety_checked']
    assert client.post(url+'/complete',headers=h,json={'story_id':s['id'],'minutes':5,'child_pages':5}).status_code==200
    assert client.post(url+'/complete',headers=h,json={'story_id':s['id'],'minutes':5,'child_pages':5}).status_code==200
    state=client.get(url,headers=h).json()
    assert state['minutes']==5 and state['words_count']==3 and state['sleep_until']
    assert client.get('/api/children',headers=h).json()[0]['reading_level']==3
    with patch.object(backend,'openai_json',generate):
        assert client.post(url+'/story',headers=h,json={'answers':['أ','ب','ج']}).status_code==409
    assert client.delete('/api/children/'+cid,headers=h).status_code==200
    assert client.get(url,headers=h).status_code==404
    with backend.SessionLocal() as db: assert db.get(backend.BedtimeProfile,cid) is None

if __name__=='__main__':
    test_health();test_auth_and_parent_isolation();test_bedtime();print('All API and bedtime checks passed')
