FROM lanshare_base

WORKDIR /app

COPY requirements.txt .
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .
