"""HTTP clients for the specialized agents.

One module per agent, each responsible only for talking to it: base URL,
request, serialization, timeout, response parsing and connection errors. DIWO
workflow rules live in services/, never here.

    cuqa_client   ->  CUQA agent   (FastAPI, default :8080)
    rdp_client    ->  RDP agent    (Flask,   default :5000)
    sctva_client  ->  SCTVA agent  (Flask,   default :8002)
"""
