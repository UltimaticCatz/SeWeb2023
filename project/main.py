from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request, Form, UploadFile
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Annotated
import random

import ZODB, ZODB.FileStorage, transaction
import BTrees.OOBTree
import persistent

#ZODB storage, called using root
storage = ZODB.FileStorage.FileStorage('data/mydata.fs')
db = ZODB.DB(storage)
connection = db.open()
root = connection.root



class Flashcard(persistent.Persistent):

    def __init__(self, term, definition):
        self.term = term
        self.definition = definition

class ChatHistory(persistent.Persistent):
    def __init__(self):
        self.messages = []

    def add_message(self, sender, message):
        self.messages.append({"sender": sender, "message": message})

    def get_messages(self):
        return self.messages
    

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


class Forum(persistent.Persistent):
    def __init__(self, username: str, title: str, description: str, file: str = "",):
        self.username = username
        self.title = title
        self.description = description
        self.file = file
        self.id = random.randint(1,100000)
        self.comments = []

    def getForumId(self):
        return self.id

    def addComment(self, comment: str):
        self.comments.append(comment)


# connection managers, for websocket chat
privateManager = ConnectionManager()
publicManager = ConnectionManager()


root.flashcardList = BTrees.OOBTree.BTree()


app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), "static")



@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/office-of-the-registra", response_class=HTMLResponse)
async def office(request: Request):
    return templates.TemplateResponse("office-of-the-registra.html", {"request": request})


@app.post("/register/{user}")
async def register_user(user: str):

    chat_history = ChatHistory()

    root.chat_history[user] = chat_history
    transaction.commit()

    return {"message": f"User {user} registered successfully."}


@app.get("/chat", response_class=HTMLResponse)
async def chat(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request, "users": root.chat_history.keys()})


@app.get("/chat/private-chat",response_class=HTMLResponse)
async def private_chat(request: Request, username: str):
    if username not in root.chat_history:
        root.chat_history[username] = ChatHistory()
    messages = root.chat_history[username].get_messages()
    return templates.TemplateResponse("private-chat.html", {"request": request, "users": root.chat_history.keys(), "username": username, "messages": messages})


#websocket connection for private chatting
@app.websocket("/chat/{username}/{user}")
async def websocket(websocket: WebSocket, username: str, user: str):
    await privateManager.connect(websocket)
    
    if username not in root.chat_history:
        root.chat_history[username] = ChatHistory()

    chat_history = root.chat_history[user]

    for message in chat_history.get_messages():
        await websocket.send_text(f"{message['sender']}: {message['message']}")

    try:
        while True:
            data = await websocket.receive_text()
            chat_history_user = root.chat_history[user]
            chat_history_user.add_message(username,data)
            
            transaction.commit()

            await privateManager.send_personal_message(f"{username} wrote: {data}", websocket)
 
    except WebSocketDisconnect:
        privateManager.disconnect(websocket)
        

    finally:
        transaction.commit()



@app.get("/chat/public-chat", response_class=HTMLResponse)
async def public_chat(request: Request, username: str):
    if username not in root.chat_history:
        root.chat_history[username] = ChatHistory()
    return templates.TemplateResponse("public-chat.html", {"request": request, "username": username})


#websocket connection for public chatting
@app.websocket("/public-chat/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await publicManager.connect(websocket)
    await publicManager.broadcast(f"{username} joined the chat")
    try:
        while True:
            data = await websocket.receive_text()
            await publicManager.broadcast(f"{username} says: {data}")
    except WebSocketDisconnect:
        publicManager.disconnect(websocket)
        await publicManager.broadcast(f"{username} left the chat")



@app.get("/forum", response_class=HTMLResponse)
async def forum(request: Request):
    return templates.TemplateResponse("forum.html", {"request": request, "forums": root.forums})


@app.get("/forum/to/{id}", response_class=HTMLResponse)
async def forum(request: Request, id: int):
    return templates.TemplateResponse("forum-id.html", {"request": request, "forums": root.forums, "id": id})


@app.post("/forum/comment/{id}", response_class=HTMLResponse)
async def comment(request: Request, comment: Annotated[str, Form()], id: int):
    for forum in root.forums:
        if forum.id == id:
            forum.addComment(comment)
    return templates.TemplateResponse("forum-id.html", {"request": request, "forums": root.forums, "id": id})

@app.get("/forum/create-forum", response_class=HTMLResponse)
async def forum(request: Request):
    return templates.TemplateResponse("create-forum.html", {"request": request})


@app.post("/forum/redirect-to-forum", response_class=HTMLResponse)
async def redirect_to_forum(request: Request, title: Annotated[str, Form()], description: Annotated[str, Form()], username: Annotated[str, Form()], file: Annotated[str, Form()] = ""):
    forum = Forum(username, title, description, file)
    root.forums.append(forum)
    transaction.commit()

    return templates.TemplateResponse("redirect-to-forum.html", {"request": request})
    


@app.get("/flashcard", response_class=HTMLResponse)
async def enter(request :Request):  
    return templates.TemplateResponse("flashcardPage.html", {"request":request})


@app.post("/save_data")
async def save_data(term: str = Form(...), definition: str = Form(...)):
    try:
        root.flashcardList[term] = Flashcard(term=term, definition=definition)
        transaction.commit()
        return {"message":"Flashcard added successfully"}
    except Exception as e:
        return {"message":f"Error:{str(e)}"}
    


keysList = list(root.flashcardList.keys())
flashcardCount = len(keysList)
currentIndex = flashcardCount - 1
@app.get("/next_card")
async def next_card():
    keysList = list(root.flashcardList.keys())
    flashcardCount = len(keysList)
    global currentIndex
    currentIndex += 1
    print("next" + str(currentIndex))
    key = keysList[currentIndex]
    flashcard = root.flashcardList[key]
    print(flashcard.term)
    print(flashcard.definition)
    flashcard_data = {
        "term": flashcard.term,
        "definition": flashcard.definition,
        "flashcardCount" : flashcardCount
    }

    return JSONResponse(content=flashcard_data)


@app.get("/prev_card")
async def prev_card():
    keysList = list(root.flashcardList.keys())
    flashcardCount = len(keysList)
    global currentIndex
    currentIndex = currentIndex - 1
    print("prev" + str(currentIndex))
    key = keysList[currentIndex]
    flashcard = root.flashcardList[key]
    print(flashcard.term)
    print(flashcard.definition)
    flashcard_data = {
        "term": flashcard.term,
        "definition": flashcard.definition,
        "flashcardCount" : flashcardCount
    }

    return JSONResponse(content=flashcard_data)

@app.get("/delete_collection")
async def delete_card():
    root.flashcardList = {}
    transaction.commit()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)