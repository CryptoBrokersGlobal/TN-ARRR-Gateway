import time
import traceback
import sharedfunc
from dbClass import dbCalls
from dbPGClass import dbPGCalls
from tnClass import tnCalls
from otherClass import otherCalls
from verification import verifier

class OtherChecker(object):
    def __init__(self, config, db = None):
        self.config = config

        if db == None:
            if self.config['main']['use-pg']:
                self.db = dbPGCalls(config)
            else:
                self.db = dbCalls(config)
        else:
            self.db = db

        self.tnc = tnCalls(config, self.db)
        self.verifier = verifier(config, self.db)
        self.otc = otherCalls(config, self.db)

        self.lastScannedBlock = self.db.lastScannedBlock("Other")

    def run(self):
        #main routine to run continuesly
        #print('INFO: started checking Other blocks at: ' + str(self.lastScannedBlock))

        while True:
            try:
                self.lastScannedBlock = self.otc.currentBlock()
                self.checkBlock()
                self.db.updHeights(self.lastScannedBlock, "Other")
            except Exception as e:
                self.lastScannedBlock -= 1
                print('ERROR: Something went wrong during Other block iteration: ' + str(traceback.TracebackException.from_exception(e)))

            time.sleep(self.config['other']['timeInBetweenChecks'])

    def checkBlock(self):
        block = self.otc.getBlock()
        for transaction in block:
            targetAddress = self.otc.checkTx(transaction)

            if targetAddress is not None:
                if targetAddress != "No attachment":
                    transaction['recipient'] = targetAddress
                    transaction['sender'] = 'unknown'

                    if not(self.tnc.validateAddress(targetAddress)):
                        self.faultHandler(transaction, "txerror")
                    else:
                        amount = float(transaction['amount'])
                        amount *= pow(10, self.config['tn']['decimals'])
                        amount = int(round(amount))

                        amountCheck = amount / pow(10, self.config['tn']['decimals'])
                        
                        if amountCheck < self.config['main']['min'] or amountCheck > self.config['main']['max']:
                            self.faultHandler(transaction, "senderror", e='outside amount ranges')
                        else:
                            try:
                                txId = None
                                self.db.insTunnel('sending', 'unknown', targetAddress)
                                txId = self.tnc.sendTx(targetAddress, amount, 'thank you for using our service.')

                                if 'error' in txId:
                                    self.faultHandler(transaction, "senderror", e=txId['message'])
                                    self.db.updTunnel("error", 'unknown', targetAddress, statusOld="sending")
                                else:
                                    print("INFO: send tx: " + str(txId))

                                    self.db.insExecuted('unknown', targetAddress, transaction['txid'], txId['id'], amountCheck, self.config['tn']['fee'])
                                    print('INFO: send tokens from other to tn!')

                                    self.db.updTunnel("verifying", 'unknown', targetAddress, statusOld='sending')
                            except Exception as e:
                                self.faultHandler(transaction, "txerror", e=e)
                                continue

                            if txId is None:
                                if targetAddress != 'invalid address':
                                    self.db.insError('unknown', targetAddress, transaction['id'], '', amountCheck, 'tx failed to send - manual intervention required')
                                    print("ERROR: tx failed to send - manual intervention required")
                                    self.db.updTunnel("error", 'unknown', targetAddress, statusOld="sending")
                            else:
                                self.tnc.verifyTx(txId, 'unknown', targetAddress)
                else:
                    self.faultHandler(transaction, 'noattachment')
        
    def faultHandler(self, tx, error, e=""):
        #handle transfers to the gateway that have problems
        amount = float(tx['amount'])
        timestampStr = sharedfunc.getnow()

        if error == "notunnel":
            self.db.insError(tx['sender'], '', '', tx['txid'], amount, 'no tunnel found for sender')
            print("ERROR: " + timestampStr + " - Error: no tunnel found for transaction from " + tx['sender'] + " - check errors table.")

        if error == "txerror":
            targetAddress = tx['recipient']
            self.db.insError(tx['sender'], targetAddress, '', tx['txid'], amount, 'tx error, possible incorrect address', str(e))
            print("ERROR: " + timestampStr + " - Error: on outgoing transaction for transaction from " + tx['sender'] + " - check errors table.")

        if error == "senderror":
            targetAddress = tx['recipient']
            self.db.insError(tx['sender'], targetAddress, '', tx['txid'], amount, 'tx error, check exception error', str(e))
            print("ERROR: " + timestampStr + " - Error: on outgoing transaction for transaction from " + tx['sender'] + " - check errors table.")
