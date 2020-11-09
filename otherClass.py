import os
import time
import bitcoinrpc.authproxy as authproxy
from dbClass import dbCalls
from dbPGClass import dbPGCalls

class otherCalls(object):
    def __init__(self, config, db = None):
        self.config = config

        if db == None:
            if self.config['main']['use-pg']:
                self.db = dbPGCalls(config)
            else:
                self.db = dbCalls(config)
        else:
            self.db = db

        self.myProxy = authproxy.AuthServiceProxy(self.config['other']['node'])

        self.lastScannedBlock = self.db.lastScannedBlock("Other")

    def currentBlock(self):
        try:
            result = self.myProxy.getblock(self.myProxy.getbestblockhash())
        except:
            self.myProxy = authproxy.AuthServiceProxy(self.config['other']['node'])
            result = self.myProxy.getblock(self.myProxy.getbestblockhash())

        return result['height']

    def getBlock(self):
        #blockhash = self.myProxy.getblockhash(height)
        #block = self.myProxy.getblock(blockhash)

        #return block
        return self.myProxy.z_listreceivedbyaddress(self.config['other']['gatewayAddress'])

    def currentBalance(self):
        balance = self.myProxy.getbalance()

        return balance

    def normalizeAddress(self, address):
        if self.validateAddress(address):
            return address
        else:
            return "invalid address"

    def validateAddress(self, address):
        try:
            return self.myProxy.z_validateaddress(address)
        except:
            return False

    def verifyTx(self, txId, sourceAddress = '', targetAddress = ''):
        if txId.startswith('opid'):
            txId = [txId]
            opRes = self.myProxy.z_getoperationresult(txId)[0]
            txId = opRes['result']['txid']

        tx = self.db.getExecuted(otherTxId=txId)

        try:
            verified = self.myProxy.gettransaction(txId)
            block = self.myProxy.getblock(verified['blockhash'])

            if verified['confirmations'] >= self.config['other']['confirmations']:
                self.db.insVerified("Other", txId, block['height'])
                print('INFO: tx to other verified!')

                self.db.delTunnel(sourceAddress, targetAddress)
            elif verified['txid'] != txId:
                print('ERROR: tx failed to send!')
                self.resendTx(txId)
        except:
            self.db.insVerified("Other", txId, 0)
            print('WARN: tx to other not verified!')

    def checkTx(self, tx):
        result = None
        #check the transaction
        if not tx['change']:
            txid = tx['txid']
            if not self.db.didWeSendTx(txid) and not self.db.didTxError(txid):
                if tx['confirmations'] > self.config['other']['confirmations']:
                    if tx['memo'].startswith('f60'):
                        result = "No attachment"
                    else:
                        import codecs
                        try:
                            recdecode = codecs.decode(tx['memo'], 'hex')
                            recipient = codecs.decode(recdecode, 'utf-8')
                            recipient = recipient.replace('\x00', '')
                            result =  recipient
                        except Exception as e:
                            result =  "No attachment"

        return result

    def sendTx(self, targetAddress, amount):
        #return '316503a028b6ccd21b503950cc191bf296291d8480adcc70505f29c1a03ee3da'

        amount -= self.config['other']['fee']

        passphrase = os.getenv(self.config['other']['passenvname'], self.config['other']['passphrase'])

        if len(passphrase) > 0:
            self.myProxy.walletpassphrase(passphrase, 30)

        fromaddress = self.config['other']['gatewayAddress']
        todata = {'address': targetAddress, 'amount': amount}
        txdata = [todata]
        opId = self.myProxy.z_sendmany(fromaddress, txdata)
        opId = [opId]
        time.sleep(5)
        txId = self.myProxy.z_getoperationresult(opId)

        while len(txId) == 0:
            time.sleep(5)
            txId = self.myProxy.z_getoperationresult(opId)

        if len(passphrase) > 0:
            self.myProxy.walletlock()

        return txId[0]

    def resendTx(self, txId):
        if type(txId) == str:
            txid = txId
        else: 
            txid = txId.hex()

        failedtx = self.db.getExecuted(otherTxId=txid)

        if len(failedtx) > 0:
            id = failedtx[0][0]
            sourceAddress = failedtx[0][1]
            targetAddress = failedtx[0][2]
            tnTxId = failedtx[0][3]
            amount = failedtx[0][6]

            self.db.insError(sourceAddress, targetAddress, tnTxId, txid, amount, 'tx failed on network - manual intervention required')
            print("ERROR: tx failed on network - manual intervention required: " + txid)
            self.db.updTunnel("error", sourceAddress, targetAddress, statusOld="verifying")

