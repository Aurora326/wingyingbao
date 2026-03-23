var portArr = {};
var sessionArr = {};

chrome.runtime.onMessageExternal.addListener(
    function (request, sender, senderCallback) {
        try {
            if (request.action == "connect") {
                connectToNativeHost(request.host, sender.tab.id, senderCallback);
            } else if (request.action == "disconnect") {
                disconnectToNativeHost(sender.tab.id, senderCallback);
            } else if (request.action == "getExtensionVersion") {
                getExtensionVersion(sender.tab.id, senderCallback);
            } else {
                sendToNativeHost(request, sender.tab.id, senderCallback);
            }

            return true;
        }
        catch (e) {
            var resultObj = new Object();
            resultObj.errorcode = 1;
            resultObj.result = e.message;
            senderCallback(resultObj);

            return true;
        }
    });

// connect to native host and get the communication port  
function connectToNativeHost(host, tabID, senderCallback) {
    try {
        if (null != portArr[tabID]) {
            portArr[tabID].disconnect();
            delete portArr[tabID];
        }

        var port = chrome.runtime.connectNative(host);
        port.onMessage.addListener(onNativeMessage);
        port.onDisconnect.addListener(onDisconnected);
        portArr[tabID] = port;

        chrome.action.enable(tabID);
        senderCallback({ "errorcode": 0 });
    }
    catch (e) {
        throw e;
    }
}

function onNativeMessage(message) {
    var receiveEventName = 'receiveNativeMsgEvent';
    if (message.randomId != undefined) {
        receiveEventName += ("." + message.randomId);
    }

    var session = sessionArr[receiveEventName];
    if (null != session) {
        session.callback(message);
        delete sessionArr[receiveEventName];
    }
}

function onDisconnected() {

}

//disconnect from native host
function disconnectToNativeHost(tabID, senderCallback) {
    try {
        if (null != portArr[tabID]) {
            portArr[tabID].disconnect();
            delete portArr[tabID];
            chrome.action.disable(tabID);
        }

        senderCallback({ "errorcode": 0 });
    }
    catch (e) {
        console.log(e.message);
    }
}

//send message from native host
function sendToNativeHost(request, tabID, senderCallback) {
    try {
        var port = portArr[tabID];
        port.postMessage(request.funcInfo);

        var receiveEventName = 'receiveNativeMsgEvent';
        if (request.funcInfo.randomId != undefined) {
            receiveEventName += ("." + request.funcInfo.randomId);
        }

        var session = new Object();
        session.port = port;
        session.tabID = tabID;
        session.callback = senderCallback;
        sessionArr[receiveEventName] = session;
    }
    catch (e) {
        throw e;
    }
}


function getExtensionVersion(tabID, senderCallback) {
    try {
        var manifest = chrome.runtime.getManifest();
        var resultObj = new Object();
        resultObj.errorcode = 0;
        resultObj.result = manifest.version;
        senderCallback(resultObj);
    }
    catch (e) {
        throw e;
    }
}