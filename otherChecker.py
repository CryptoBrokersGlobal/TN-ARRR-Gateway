import os
import sqlite3 as sqlite
import time
import PyCWaves
import traceback
import sharedfunc
import bitcoinrpc.authproxy as authproxy
from verification import verifier

class OtherChecker(object):
    def __init__(self, config):
        self.config = config
        self.dbCon = sqlite.connect('gateway.db')
        self.myProxy = authproxy.AuthServiceProxy(self.config['other']['node'])

        self.pwTN = PyCWaves.PyCWaves()
        self.pwTN.setNode(node=self.config['tn']['node'], chain=self.config['tn']['network'], chain_id='L')
        self.pwTN.THROW_EXCEPTION_ON_ERROR = True
        seed = os.getenv(self.config['tn']['seedenvname'], self.config['tn']['gatewaySeed'])
        self.tnAddress = self.pwTN.Address(seed=seed)
        self.tnAsset = self.pwTN.Asset(self.config['tn']['assetId'])
        self.verifier = verifier(config)

        cursor = self.dbCon.cursor()
        self.lastScannedBlock = cursor.execute('SELECT height FROM heights WHERE chain = "Other"').fetchall()[0][0]

    def getCurrentBlock(self):
        try:
            latestBlock = self.myProxy.getblock(self.myProxy.getbestblockhash())
        except:
            self.myProxy = authproxy.AuthServiceProxy(self.config['other']['node'])
            latestBlock = self.myProxy.getblock(self.myProxy.getbestblockhash())

        return latestBlock['height']

    def getBlock(self, height):
        blockhash = self.myProxy.getblockhash(height)
        block = self.myProxy.getblock(blockhash)

        return block

    def run(self):
        #main routine to run continuesly
        print('started checking Other blocks at: ' + str(self.lastScannedBlock))

        self.dbCon = sqlite.connect('gateway.db')
        while True:
            try:
                self.checkBlock()

                cursor = self.dbCon.cursor()
                cursor.execute('UPDATE heights SET "height" = ' + str(self.getCurrentBlock()) + ' WHERE "chain" = "Other"')
                self.dbCon.commit()
            except Exception as e:
                print('Something went wrong during Other block iteration: ')
                print(traceback.TracebackException.from_exception(e))
                self.myProxy = authproxy.AuthServiceProxy(self.config['other']['node'])


            time.sleep(self.config['other']['timeInBetweenChecks'])

    def checkBlock(self):
        #check content of the block for valid transactions
        txInfo = self.checkTx(self.config['other']['gatewayAddress'])

        if txInfo is not None:
            try:
                verAddr = self.pwTN.validateAddress(txInfo['recipient'])
            except:
                self.faultHandler(txInfo, 'txerror', txInfo)
                
            if not self.pwTN.validateAddress(txInfo['recipient']):
                self.faultHandler(txInfo, 'txerror', txInfo)
            else:
                targetAddress = txInfo['recipient']
                amount = float(txInfo['amount'])
                amount -= self.config['tn']['fee']
                amount *= pow(10, self.config['tn']['decimals'])
                amount = int(round(amount))

                if amount <= 0:
                    self.faultHandler(txInfo, "senderror", e='under minimum amount')
                else:
                    try:
                        addr = self.pwTN.Address(targetAddress)
                        if self.config['tn']['assetId'] == 'TN':
                            tx = self.tnAddress.sendWaves(addr, amount, 'Thanks for using our service!', txFee=2000000)
                        else:
                            tx = self.tnAddress.sendAsset(addr, self.tnAsset, amount, 'Thanks for using our service!', txFee=2000000)

                        if 'error' in tx:
                            self.faultHandler(txInfo, "senderror", e=tx['message'])
                        else:
                            print("send tx: " + str(tx))

                            cursor = self.dbCon.cursor()
                            amount /= pow(10, self.config['tn']['decimals'])
                            cursor.execute('INSERT INTO executed ("sourceAddress", "targetAddress", "otherTxId", "tnTxId", "amount", "amountFee") VALUES ("Unknown", "' + targetAddress + '", "' + txInfo['id'] + '", "' + tx['id'] + '", "' + str(round(amount)) + '", "' + str(self.config['tn']['fee']) + '")')
                            self.dbCon.commit()
                            print(self.config['main']['name'] + ' tokens deposited on tn!')
                    except Exception as e:
                        self.faultHandler(txInfo, "txerror", e=e)

                    self.verifier.verifyTN(tx)

    def checkTx(self, tx):
        #check the transaction
        result = None
        transactions = self.myProxy.z_listreceivedbyaddress(tx)

        if len(transactions) > 0:
            cursor = self.dbCon.cursor()
            for transaction in transactions:
                if not transaction['change']:
                    res = cursor.execute('SELECT tnTxId FROM executed WHERE otherTxId = "' + transaction['txid'] + '"').fetchall()
                    res2 = cursor.execute('SELECT 1 FROM errors WHERE otherTxId = "' + transaction['txid'] + '"').fetchall()

                    if len(res) == 0 and len(res2) == 0: 
                        if transaction['rawconfirmations'] > self.config['other']['confirmations']:
                            if transaction['memo'].startswith('f60'):
                                self.faultHandler(transaction, "receiveerror", "empty memo field")

                                return result
                            
                            import codecs
                            try:
                                recdecode = codecs.decode(transaction['memo'], 'hex')
                                recipient = codecs.decode(recdecode, 'utf-8')
                                recipient = recipient.replace('\x00', '')
                                amount = transaction['amount']
                                result =  { 'recipient': recipient, 'function': 'transfer', 'amount': amount, 'id': transaction['txid'], 'sender': 'Unknown' }
                            except Exception as e:
                                self.faultHandler(transaction, "receiveerror", e=e)

                            return result

        return result
        

    def faultHandler(self, tx, error, e="", senders=object):
        #handle transfers to the gateway that have problems
        amount = tx['amount']
        timestampStr = sharedfunc.getnow()

        if error == "notunnel":
            cursor = self.dbCon.cursor()
            cursor.execute('INSERT INTO errors ("sourceAddress", "targetAddress", "tnTxId", "otherTxId", "amount", "error") VALUES ("' + tx['sender'] + '", "", "", "' + tx['id'] + '", "' + str(amount) + '", "no tunnel found for sender")')
            self.dbCon.commit()
            print(timestampStr + " - Error: no tunnel found for transaction from " + tx['sender'] + " - check errors table.")

        if error == "txerror":
            targetAddress = tx['recipient']
            cursor = self.dbCon.cursor()
            cursor.execute('INSERT INTO errors ("sourceAddress", "targetAddress", "tnTxId", "otherTxId", "amount", "error", "exception") VALUES ("' + tx['sender'] + '", "' + targetAddress + '", "", "' + tx['id'] + '", "' + str(amount) + '", "tx error, possible incorrect address", "' + str(e) + '")')
            self.dbCon.commit()
            print(timestampStr + " - Error: on outgoing transaction for transaction from " + tx['sender'] + " - check errors table.")

        if error == "senderror":
            targetAddress = tx['recipient']
            cursor = self.dbCon.cursor()
            cursor.execute('INSERT INTO errors ("sourceAddress", "targetAddress", "tnTxId", "otherTxId", "amount", "error", "exception") VALUES ("' + tx['sender'] + '", "' + targetAddress + '", "", "' + tx['id'] + '", "' + str(amount) + '", "tx error, check exception error", "' + str(e) + '")')
            self.dbCon.commit()
            print(timestampStr + " - Error: on outgoing transaction for transaction from " + tx['sender'] + " - check errors table.")

        if error == "receiveerror":
            targetAddress = tx['memo']
            cursor = self.dbCon.cursor()
            cursor.execute('INSERT INTO errors ("sourceAddress", "targetAddress", "tnTxId", "otherTxId", "amount", "error", "exception") VALUES ("Unknown", "' + targetAddress + '", "", "' + tx['txid'] + '", "' + str(amount) + '", "receive error, check exception error", "' + str(e) + '")')
            self.dbCon.commit()
            print(timestampStr + " - Error: on incoming transaction - check errors table.")
