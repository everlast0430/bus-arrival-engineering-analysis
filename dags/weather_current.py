from airflow import DAG
from airflow.decorators import task
from airflow.models import Variable
from airflow.providers.postgres.hooks.postgres import PostgresHook
from datetime import datetime, timedelta

import requests
import pendulum
import logging


def get_Redshift_connection(autocommit=False):
    hook = PostgresHook(postgres_conn_id='redshift_dev_db')
    conn = hook.get_conn()
    conn.autocommit = autocommit
    return conn.cursor()

# 수원 날씨정보 가져오기
@task(task_id='py_extract',
      params={'url' : Variable.get("open_weather_api_url"),
              'key' : Variable.get("open_weather_api_key"),
              'city' : "Suwon",
              'lang' : "kr",
              'metric' : "metric"
              })
def extract(**kwargs):
    params = kwargs.get('params')
    city = params['city']
    key = params['key']
    lang = params['lang']
    metric = params['metric']
    url = params['url']
    #url = url.format(city, key, lang, metric)
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={key}&lang={lang}&units={metric}"
    r = requests.get(url)

    try:
        return r.json()
    except Exception as e:
        logging.info(r.text)
        raise e

# 날씨 정보 전처리
@task(task_id='py_transform')
def transform(**kwargs):
    value = kwargs['ti'].xcom_pull(task_ids='py_extract')
    city = value['name']
    weather_condition = value['weather'][0]['main']
    created_at = datetime.fromtimestamp(value['dt']).strftime('%Y-%m-%d %H:%M:%S')
    
    return (city, weather_condition, created_at)

# 날씨 데이터 적재
@task(task_id='py_load')
def load(**kwargs):
    cur = get_Redshift_connection()
    value = kwargs['ti'].xcom_pull(task_ids='py_transform')
    city = value[0]
    weather_condition = value[1]
    created_at = value[2]
    schema = 'dev.adhoc'
    table = 'WEATHER_CURRENT'
    
    insert_sql = f"INSERT INTO {schema}.{table} VALUES ('{created_at}', '{weather_condition}', '{city}')"
    
    try:
        cur.execute(insert_sql)
        cur.execute("COMMIT;")
    except Exception as e:
        cur.execute("ROLLBACK;")
        logging.info(insert_sql)
        raise e

with DAG(
    dag_id="weather_current",
    schedule="*/10 6-8 * * *",
    start_date=pendulum.datetime(2024, 3, 1, tz="Asia/Seoul"),
    catchup=False,
    tags=['weather'],
    default_args = {
        'retries': 1,
        'retry_delay': timedelta(minutes=1),
    }
) as dag:
    
    extract() >> transform() >> load()
