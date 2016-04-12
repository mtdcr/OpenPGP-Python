# Pure Python implementation of OpenPGP <http://tools.ietf.org/html/rfc4880>
# Port of openpgp-php <http://github.com/bendiken/openpgp-php>

from struct import pack, unpack
from time import time
from math import ceil, floor, log
import zlib, bz2, base64, textwrap, hashlib, re, sys, itertools

__all__ = [
    'Message',
    'S2K',

    # Packets
    'Packet',
    'AsymmetricSessionKeyPacket',
    'CompressedDataPacket',
    'EmbeddedSignaturePacket',
    'EncryptedDataPacket',
    'ExperimentalPacket',
    'IntegrityProtectedDataPacket',
    'LiteralDataPacket',
    'MarkerPacket',
    'ModificationDetectionCodePacket',
    'OnePassSignaturePacket',
    'OpenPGPException',
    'PublicKeyPacket',
    'PublicSubkeyPacket',
    'SecretKeyPacket',
    'SecretSubkeyPacket',
    'SignaturePacket',
    'SymmetricSessionKeyPacket',
    'TrustPacket',
    'UserAttributePacket',
    'UserIDPacket',

    # Utilities
    'unarmor',
    'enarmor',
    'crc24',
    'checksum',
    'bitlength',
    'PushbackGenerator'
]

def unarmor(text):
    """ Convert ASCII-armored data into binary
        http://tools.ietf.org/html/rfc4880#section-6
        http://tools.ietf.org/html/rfc2045
    """
    result = []
    chunks = re.findall(r'\n-----BEGIN [^-]+-----\n(.*?)\n-----END [^-]+-----\n', "\n" + text.replace("\r\n", "\n").replace("\r", "\n") + "\n", re.S)

    for chunk in chunks:
        headers, data = chunk.split("\n\n")
        crc = data[-5:]
        data = base64.b64decode(data[:-5].encode('utf-8'))
        if crc[0] != '=':
            raise OpenPGPException('CRC24 check failed')
        if crc24(data) != unpack('!L', b'\0' + base64.b64decode(crc[1:].encode('utf-8')))[0]:
            raise OpenPGPException('CRC24 check failed')
        result.append((headers, data))

    return result

def crc24(data):
    """
        http://tools.ietf.org/html/rfc4880#section-6
        http://tools.ietf.org/html/rfc4880#section-6.1
    """
    crc = 0x00b704ce
    for i in range(0, len(data)):
        crc ^= (ord(data[i:i+1]) & 255) << 16
        for j in range(0, 8):
            crc <<= 1
            if (crc & 0x01000000):
                crc ^= 0x01864cfb
    return crc & 0x00ffffff

def enarmor(data, marker='PUBLIC KEY BLOCK', headers=None, lineWidth=64):
    """
    @see http://tools.ietf.org/html/rfc4880#section-6.2 OpenPGP Message Format / Ascii Armor
    @see http://tools.ietf.org/html/rfc2045 Base64 encoding

    @param data: binary data to encode
    @type  data: bytes

    @param marker: The header line text is chosen based upon the type
        of data that is being encoded in Armor, and how it is being encoded.
        Header line texts include the following strings:
            - MESSAGE
            - PUBLIC KEY BLOCK
            - PRIVATE KEY BLOCK
            - MESSAGE, PART X/Y
            - MESSAGE, PART X
            - SIGNATURE
    @type  marker: str

    @param headers: key value, e.g {'Version' : 'GnuPG v2.0.22 (MingW32)'}
    @type  headers: None | generator[(str, str)]

    @param lineWidth: GnuPG uses 64 bit, RFC4880 limits to 76
    @type  lineWidth: int

    @rtype: str
    """

    def _iter_enarmor(data):
        """
        @type data: bytes

        @param marker: Specifies the kind of data to armor
        @type  marker: str

        @param headers: optional header fields
            (dict keys will be sorted lexicographically)
        @type  headers: dict | [(keyString, valueString)] | None

        @rtype: generator[str]
        """
        yield '-----BEGIN PGP ' + str(marker).upper() + '-----'
        headersDict = headers or {}
        try:
            headerItems = list(headersDict.iteritems())
            headerItems.sort()
        except AttributeError: # list has no 'iteritems'
            headerItems = list(headersDict) # already list of key-value.pairs
        for (key, value) in headerItems:
            yield "%(key)s: %(value)s" % locals()
        yield '' # empty line

        text = base64.b64encode(data) # bytes in Python 3!
        try:
            # Python 3
            textStr = text.decode('ascii')
        except Exception:
            # Python 2
            textStr = text
        # max 76 chars per line!
        for line in textwrap.wrap(textStr, width = lineWidth):
            yield line
        # unsigned long with 4 bypte/32 bit in byte-order Big Endian
        checksumBytes = pack('>L', crc24(data))
        checksumBase64 = base64.b64encode(checksumBytes[1:])  # bytes in Python 3!
        try:
            # Python 3
            checksumStr = checksumBase64.decode('ascii')
        except Exception:
            # Python 2
            checksumStr = checksumBase64
        yield '=' + str(checksumStr) # take only the last 3 bytes
        yield '-----END PGP ' + str(marker).upper() + '-----'
        yield '' # final line break
        return

    return "\n".join(_iter_enarmor(data))

def bitlength(data):
    """ http://tools.ietf.org/html/rfc4880#section-12.2 """
    if ord(data[0:1]) == 0:
        raise OpenPGPException("Tried to get bitlength of string with leading 0")
    return (len(data) - 1) * 8 + int(floor(log(ord(data[0:1]), 2))) + 1

def checksum(data):
    mkChk = 0
    for i in range(0, len(data)):
        mkChk = (mkChk + ord(data[i:i+1])) % 65536
    return mkChk

def _gen_one(i):
    yield i

def _ensure_bytes(n, chunk, g):
    while len(chunk) < n:
        chunk += next(g)
    return chunk

def _slurp(g):
    bs = b''
    for chunk in g:
        bs += chunk
    return bs

class OpenPGPException(Exception):
    pass # Everything inherited

class S2K(object):
    def __init__(self, salt=b'BADSALT', hash_algorithm=10, count=65536, type=3):
        self.type = type
        self.hash_algorithm = hash_algorithm
        self.salt = salt
        self.count = count

    def to_bytes(self):
        bs = pack('!B', self.type)
        if self.type in [0,1,3]:
            bs += pack('!B', self.hash_algorithm)
        if self.type in [1,3]:
            bs += self.salt
        if self.type in [3]:
            bs += pack('!B', self.encode_s2k_count(self.count))
        return bs

    def raw_hash(self, s, prefix=b''):
        hasher = hashlib.new(SignaturePacket.hash_algorithms[self.hash_algorithm].lower())
        hasher.update(prefix)
        hasher.update(s)
        return hasher.digest()

    def iterate(self, s, prefix=b''):
        hasher = hashlib.new(SignaturePacket.hash_algorithms[self.hash_algorithm].lower())
        hasher.update(prefix)
        hasher.update(s)
        remaining = self.count - len(s)
        while remaining > 0:
            hasher.update(s[0:remaining])
            remaining -= len(s)
        return hasher.digest()

    def sized_hash(self, hasher, s, size):
        hsh = hasher(s)
        prefix = b'\0'
        while len(hsh) < size:
            hsh += hasher(s, prefix)
            prefix += b'\0'

        return hsh[0:size]

    def make_key(self, passphrase, size):
        if self.type == 0:
            return self.sized_hash(self.raw_hash, passphrase, size)
        elif self.type == 1:
            return self.sized_hash(self.raw_hash, self.salt + passphrase, size)
        elif self.type == 3:
            return self.sized_hash(self.iterate, self.salt + passphrase, size)

    @classmethod
    def parse(cls, input_or_g):
        if hasattr(input_or_g, 'next') or hasattr(input_or_g, '__next__'):
            g = PushbackGenerator(input_or_g)
        else:
            g = PushbackGenerator(_gen_one(input_or_g))

        chunk = _ensure_bytes(1, next(g), g)
        s2k_type = ord(chunk[0:1])
        if s2k_type == 0:
            chunk = _ensure_bytes(2, chunk, g)
            if len(chunk) > 2:
                g.push(chunk[2:])
            return (cls(b'UNSALTED', ord(chunk[1:2]), 0, s2k_type), 2)
        elif s2k_type == 1:
            chunk = _ensure_bytes(10, chunk, g)
            if len(chunk) > 10:
                g.push(chunk[10:])
            return (cls(chunk[2:10], ord(chunk[1:2]), 0, s2k_type), 10)
        elif s2k_type == 3:
            chunk = _ensure_bytes(11, chunk, g)
            if len(chunk) > 11:
                g.push(chunk[11:])
            return (cls(chunk[2:10], ord(chunk[1:2]), cls.decode_s2k_count(ord(chunk[10:11])), s2k_type), 11)

    @classmethod
    def decode_s2k_count(cls, c):
        return int(16 + (c & 15)) << ((c >> 4) + 6)

    @classmethod
    def encode_s2k_count(cls, iterations):
        if iterations >= 65011712:
            return 255

        count = iterations >> 6
        c = 0
        while count >= 32:
            count = count >> 1
            c += 1

        result = (c << 4) | (count - 16)

        if cls.decode_s2k_count(result) < iterations:
            return result + 1

        return result

    def __repr__(self):
        return "%s: %s" % (type(self), self.__dict__.__repr__())

    def __eq__(self, other):
        if type(other) is type(self):
            return self.__dict__ == other.__dict__
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

class PushbackGenerator(object):
    def __init__(self, g):
        self._g = g
        self._pushback = []

    def __iter__(self):
        return self

    def next(self):
        return self.__next__()

    def __next__(self):
        if len(self._pushback):
            return self._pushback.pop(0)
        return next(self._g)

    def hasNext(self):
        if len(self._pushback) > 0:
            return True
        try:
            chunk = next(self)
            self.push(chunk)
            return True
        except StopIteration:
            return False

    def push(self, i):
        if hasattr(self._g, 'push'):
            self._g.push(i)
        else:
            self._pushback.insert(0, i)

class Message(object):
    """ Represents an OpenPGP message (set of packets)
        http://tools.ietf.org/html/rfc4880#section-4.1
        http://tools.ietf.org/html/rfc4880#section-11
        http://tools.ietf.org/html/rfc4880#section-11.3
    """
    @classmethod
    def parse(cls, input_data):
        """ http://tools.ietf.org/html/rfc4880#section-4.1
            http://tools.ietf.org/html/rfc4880#section-4.2
        """
        m = Message([]) # Nothing parsed yet
        if hasattr(input_data, 'next') or hasattr(input_data, '__next__'):
            m._input = PushbackGenerator(input_data)
        else:
            m._input = PushbackGenerator(_gen_one(input_data))

        return m

    def __init__(self, packets=[]):
        self._packets_start = packets
        self._packets_end = []
        self._input = None

    def to_bytes(self):
        b = b''
        for p in self:
            b += p.to_bytes()
        return b

    def signatures(self):
        """ Extract signed objects from a well-formatted message
            Recurses into CompressedDataPacket
            http://tools.ietf.org/html/rfc4880#section-11
        """
        msg = self
        while isinstance(msg[0], CompressedDataPacket):
            msg = msg[0]

        key = None
        userid = None
        subkey = None
        sigs = []
        final_sigs = []

        for p in msg:
            if isinstance(p, LiteralDataPacket):
                return [(p, list(filter(lambda x: isinstance(x, SignaturePacket), msg)))]
            elif isinstance(p, PublicSubkeyPacket) or isinstance(p, SecretSubkeyPacket):
                if userid:
                    final_sigs.append((key, userid, sigs))
                    userid = None
                elif subkey:
                    final_sigs.append((key, subkey, sigs))
                    key = None
                sigs = []
                subkey = p
            elif isinstance(p, PublicKeyPacket):
                if userid:
                    final_sigs.append((key, userid, sigs))
                    userid = None
                elif subkey:
                    final_sigs.append((key, subkey, sigs))
                    subkey = None
                elif key:
                    final_sigs.append((key, sigs))
                    key = None
                sigs = []
                key = p
            elif isinstance(p, UserIDPacket):
                if userid:
                    final_sigs.append((key, userid, sigs))
                    userid = None
                elif key:
                    final_sigs.append((key, sigs))
                sigs = []
                userid = p
            elif isinstance(p, SignaturePacket):
                sigs.append(p)

        if userid:
            final_sigs.append((key, userid, sigs))
        elif subkey:
            final_sigs.append((key, subkey, sigs))
        elif key:
            final_sigs.append((key, sigs))

        return final_sigs

    def verified_signatures(self, verifiers):
       """ Function to extract verified signatures
           verifiers is an array of callbacks formatted like {'RSA': {'SHA256': CALLBACK}} that take two parameters: raw message and signature packet
       """
       signed = self.signatures()
       vsigned = []

       for sign in signed:
           vsigs = []
           for sig in sign[-1]:
               verifier = verifiers[sig.key_algorithm_name()][sig.hash_algorithm_name()]
               if verifier and self.verify_one(verifier, sign, sig):
                   vsigs.append(sig)
           vsigned.append(sign[:-1] + (vsigs,))

       return vsigned

    def verify_one(self, verifier, sign, sig):
        raw = None
        if isinstance(sign[0], LiteralDataPacket):
            sign[0].normalize()
            raw = sign[0].data
        elif len(sign) > 1 and isinstance(sign[1], UserIDPacket):
            raw = b''.join(sign[0].fingerprint_material() + [pack('!B', 0xB4),
                  pack('!L', len(sign[1].body())), sign[1].body()])
        elif len(sign) > 1 and (isinstance(sign[1], PublicSubkeyPacket) or isinstance(sign[1], SecretSubkeyPacket)):
            raw = b''.join(sign[0].fingerprint_material() + sign[1].fingerprint_material())
        elif isinstance(sign[0], PublicKeyPacket):
            raw = sign[0].fingerprint_material()
        else:
            raw = None

        return verifier(raw + sig.trailer, sig)

    def force(self):
        packets = []
        for p in self:
            packets.append(p)
        return packets

    def __iter__(self):
        # Already parsed packets
        for p in self._packets_start:
            yield p

        if self._input:
            while self._input.hasNext():
                packet = Packet.parse(self._input)
                if packet:
                    self._packets_start.append(packet)
                    yield packet
                else:
                    raise OpenPGPException("Parsing is stuck")
            self._input = None # Parsing done

        # Appended packets
        for p in self._packets_end:
            yield p

    def __getitem__(self, item):
        i = 0
        for p in self:
            if i == item:
                return p
            i += 1

    def append(self, item):
        self._packets_end.append(item)

    def __repr__(self):
        return "%s: %s" % (type(self), self.__dict__.__repr__())

    def __eq__(self, other):
        if type(other) is type(self):
            return self.force() == other.force()
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

class Packet(object):
    """ OpenPGP packet.
        http://tools.ietf.org/html/rfc4880#section-4.1
        http://tools.ietf.org/html/rfc4880#section-4.3
    """

    @classmethod
    def parse(cls, input_data):
        if hasattr(input_data, 'next') or hasattr(input_data, '__next__'):
            g = PushbackGenerator(input_data)
        else:
            g = PushbackGenerator(_gen_one(input_data))

        packet = None
        # If there is not even one byte, then there is no packet at all
        chunk = _ensure_bytes(1, next(g), g)
        try:
            # Parse header
            if ord(chunk[0:1]) & 64:
                tag, data_length = Packet.parse_new_format(chunk, g)
            else:
                tag, data_length = Packet.parse_old_format(chunk, g)

            if not data_length:
                chunk = _slurp(g)
                data_length = len(chunk)
                g.push(chunk)

            if tag:
              try:
                  packet_class = Packet.tags[tag]
              except KeyError:
                  packet_class = Packet

              packet = packet_class()
              packet.tag = tag
              packet.input = g
              packet.length = data_length
              packet.read()
              packet.read_bytes(packet.length) # Remove excess bytes
              packet.input = None
              packet.length = None
        except StopIteration:
             raise OpenPGPException("Not enough bytes")

        return packet

    @classmethod
    def parse_new_format(cls, chunk, g):
       """ Parses a new-format (RFC 4880) OpenPGP packet.
           http://tools.ietf.org/html/rfc4880#section-4.2.2
       """

       chunk = _ensure_bytes(2, chunk, g)
       tag = ord(chunk[0:1]) & 63
       length = ord(chunk[1:2])

       if length < 192: # One octet length
         if len(chunk) > 2:
             g.push(chunk[2:])
         return (tag, length)
       if length > 191 and length < 224: # Two octet length
         chunk = _ensure_bytes(3, chunk, g)
         if len(chunk) > 2:
             g.push(chunk[3:])
         return (tag, ((length - 192) << 8) + ord(chunk[2:3]) + 192)
       if length == 255: # Five octet length
         chunk = _ensure_bytes(6, chunk, g)
         if len(chunk) > 6:
             g.push(chunk[6:])
         return (tag, unpack('!L', chunk[2:6])[0])
       # TODO: Partial body lengths. 1 << ($len & 0x1F)

    @classmethod
    def parse_old_format(cls, chunk, g):
        """ Parses an old-format (PGP 2.6.x) OpenPGP packet.
            http://tools.ietf.org/html/rfc4880#section-4.2.1
        """
        chunk = _ensure_bytes(1, chunk, g)
        tag = ord(chunk[0:1])
        length = tag & 3
        tag = (tag >> 2) & 15
        if length == 0: # The packet has a one-octet length. The header is 2 octets long.
            head_length = 2
            chunk = _ensure_bytes(head_length, chunk, g)
            data_length = ord(chunk[1:2])
        elif length == 1: # The packet has a two-octet length. The header is 3 octets long.
            head_length = 3
            chunk = _ensure_bytes(head_length, chunk, g)
            data_length = unpack('!H', chunk[1:3])[0]
        elif length == 2: # The packet has a four-octet length. The header is 5 octets long.
            head_length = 5
            chunk = _ensure_bytes(head_length, chunk, g)
            data_length = unpack('!L', chunk[1:5])[0]
        elif length == 3: # The packet is of indeterminate length. The header is 1 octet long.
            head_length = 1
            chunk = _ensure_bytes(head_length, chunk, g)
            data_length = None

        if len(chunk) > head_length:
             g.push(chunk[head_length:])
        return (tag, data_length)

    def __init__(self, data=None):
        for tag in Packet.tags:
            if Packet.tags[tag] == self.__class__:
                self.tag = tag
                break
        self.data = data

    def read(self):
        # Will normally be overridden by subclasses
        self.data = self.read_bytes(self.length)

    def body(self):
        return self.data # Will normally be overridden by subclasses

    def header_and_body(self):
        body = self.body() # Get body first, we will need it's length
        length = body and len(body) or 0
        # Prefer smallest possible header. The 2nd most significant bit
        # of the tag determines the packet format version.
        if self.tag < 16:
                # Old packet format
                if length <= 0xFF:
                        size = pack('!B', length)
                        li = 0
                elif length <= 0xFFFF:
                        size = pack('!H', length)
                        li = 1
                else:
                        size = pack('!L', length)
                        li = 2
                tag = pack('!B', 0x80 | (self.tag << 2) | li)
        else:
                # New packet format
                if length <= 191:
                        size = pack('!B', length)
                elif length <= 8383:
                        size = pack('!H', length - 192)
                else:
                        size = pack('!B', 255) + pack('!L', length)
                tag = pack('!B', 0xC0 | self.tag)
        return {'header': tag + size, 'body': body }

    def to_bytes(self):
        data = self.header_and_body()
        return data['header'] + (data['body'] and data['body'] or b'')

    def read_timestamp(self):
        """ ttp://tools.ietf.org/html/rfc4880#section-3.5 """
        return self.read_unpacked(4, '!L')

    def read_mpi(self):
       """ http://tools.ietf.org/html/rfc4880#section-3.2 """
       length = self.read_unpacked(2, '!H') # length in bits
       length = (int)((length + 7) / 8) # length in bytes
       return self.read_bytes(length)

    def read_unpacked(self, count, fmt):
        """ http://docs.python.org/library/struct.html """
        unpacked = unpack(fmt, self.read_bytes(count))
        return unpacked[0] # unpack returns tuple

    def read_byte(self):
      byte = self.read_bytes(1)
      return byte and byte[0:1] or None

    def read_bytes(self, count):
        chunk = _ensure_bytes(count, b'', self.input)
        if len(chunk) > count:
            self.input.push(chunk[count:])
        self.length -= count
        return chunk[:count]

    tags = {} # Actual data at end of file

    def __repr__(self):
        return "%s: %s" % (type(self), self.__dict__.__repr__())

    def __eq__(self, other):
        if type(other) is type(self):
            return self.__dict__ == other.__dict__
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

class AsymmetricSessionKeyPacket(Packet):
    """ OpenPGP Public-Key Encrypted Session Key packet (tag 1).
        http://tools.ietf.org/html/rfc4880#section-5.1
    """
    def __init__(self, key_algorithm='', keyid='', encrypted_data='', version=3):
        super(AsymmetricSessionKeyPacket, self).__init__()
        self.version = version
        self.keyid = keyid[-16:]
        self.key_algorithm = key_algorithm
        self.encrypted_data = encrypted_data

    def read(self):
        self.version = ord(self.read_byte())
        if self.version == 3:
            rawkeyid = self.read_bytes(8)
            self.keyid = '';
            for i in range(0, len(rawkeyid)): # Store KeyID in Hex
                self.keyid += '%02X' % ord(rawkeyid[i:i+1])

            self.key_algorithm = ord(self.read_byte())
            self.encrypted_data = self.read_bytes(self.length)
        else:
            raise OpenPGPException("Unsupported AsymmetricSessionKeyPacket version: " + self.version)

    def body(self):
        b = pack('!B', self.version)

        for i in range(0, len(self.keyid), 2):
            b += pack('!B', int(self.keyid[i] + self.keyid[i+1], 16))

        b += pack('!B', self.key_algorithm)
        b += self.encrypted_data
        return b

class SignaturePacket(Packet):
    """ OpenPGP Signature packet (tag 2).
        http://tools.ietf.org/html/rfc4880#section-5.2
    """
    def __init__(self, data=None, key_algorithm=None, hash_algorithm=None):
        super(SignaturePacket, self).__init__()
        self.version = 4 # Default to version 4 sigs
        self.hash_algorithm = hash_algorithm
        self.hashed_subpackets = self.unhashed_subpackets = []
        if isinstance(self.hash_algorithm, str):
            for a in SignaturePacket.hash_algorithms:
                if SignaturePacket.hash_algorithms[a] == self.hash_algorithm:
                    self.hash_algorithm = a
                    break
        self.key_algorithm = key_algorithm
        if isinstance(self.key_algorithm, str):
            for a in PublicKeyPacket.algorithms:
                if PublicKeyPacket.algorithms[a] == self.key_algorithm:
                    self.key_algorithm = a
                    break
        if data: # If we have any data, set up the creation time
            self.hashed_subpackets = [self.SignatureCreationTimePacket(time())]
        if isinstance(data, LiteralDataPacket):
            self.signature_type = data.format != 'b' and 0x01 or 0x00
            data.normalize()
            data = data.data
        elif hasattr(data, 'encode'):
            data = data.encode('utf-8')
        elif isinstance(data, Message) and isinstance(data[0], PublicKeyPacket):
            # data is a message with PublicKey first, UserID second
            key = b''.join(data[0].fingerprint_material())
            user_id = data[1].body()
            data = key + pack('!B', 0xB4) + pack('!L', len(user_id)) + user_id
        self.data = data # Store to-be-signed data in here until the signing happens
        self.trailer = None
        self.hash_head = None

    def sign_data(self, signers):
        """ self.data must be set to the data to sign (done by constructor)
            signers in the same format as verifiers for Message.
        """
        self.trailer = self.calculate_trailer()
        signer = signers[self.key_algorithm_name()][self.hash_algorithm_name()]
        data = signer(self.data + self.trailer)
        self.data = []
        for mpi in data:
            if sys.version_info[0] == 2 and isinstance(mpi, long) or isinstance(mpi, int):
                if hasattr(mpi, 'to_bytes'):
                    self.data.append(mpi.to_bytes(ceil(mpi.bit_length() / 8), byteorder='big'))
                else: # For python 2
                    hex_mpi = '%02X' % mpi
                    if len(hex_mpi) % 2 != 0:
                        hex_mpi = '0' + hex_mpi
                    final = b''
                    for i in range(0, len(hex_mpi), 2):
                        final += pack('!B', int(hex_mpi[i:i+2], 16))
                    self.data.append(final)
            else:
                self.data.append(mpi)
        self.hash_head = unpack('!H', b''.join(self.data)[0:2])[0]

    def read(self):
        self.version = ord(self.read_byte())
        if self.version == 2 or self.version == 3:
            assert(ord(self.read_byte()) == 5);
            self.signature_type = ord(self.read_byte())
            creation_time = self.read_timestamp()
            keyid = self.read_bytes(8)
            keyidHex = '';
            for i in range(0, len(keyid)): # Store KeyID in Hex
                keyidHex += '%02X' % ord(keyid[i:i+1])

            self.hashed_subpackets = []
            self.unhashed_subpackets = [
                SignaturePacket.SignatureCreationTimePacket(creation_time),
                SignaturePacket.IssuerPacket(keyidHex)
            ]

            self.key_algorithm = ord(self.read_byte())
            self.hash_algorithm = ord(self.read_byte())
            self.hash_head = self.read_unpacked(2, '!H')
            self.data = []
            while self.length > 0:
                self.data += [self.read_mpi()]
        elif self.version == 4:
            self.signature_type = ord(self.read_byte())
            self.key_algorithm = ord(self.read_byte())
            self.hash_algorithm = ord(self.read_byte())
            self.trailer = pack('!B', 4) + pack('!B', self.signature_type) + pack('!B', self.key_algorithm) + pack('!B', self.hash_algorithm)

            hashed_size = self.read_unpacked(2, '!H')
            hashed_subpackets = self.read_bytes(hashed_size)
            self.trailer += pack('!H', hashed_size) + hashed_subpackets
            self.hashed_subpackets = self.get_subpackets(hashed_subpackets)

            self.trailer += pack('!B', 4) + pack('!B', 0xff) + pack('!L', 6 + hashed_size)

            unhashed_size = self.read_unpacked(2, '!H')
            self.unhashed_subpackets = self.get_subpackets(self.read_bytes(unhashed_size))

            self.hash_head = self.read_unpacked(2, '!H')
            self.data = []
            while self.length > 0:
                self.data += [self.read_mpi()]

    def calculate_trailer(self):
        # The trailer is just the top of the body plus some crap
        body = self.body_start()
        return body + pack('!B', 4) + pack('!B', 0xff) + pack('!L', len(body))

    def body_start(self):
        body = pack('!B', 4) + pack('!B', self.signature_type) + pack('!B', self.key_algorithm) + pack('!B', self.hash_algorithm)

        hashed_subpackets = b''
        for p in self.hashed_subpackets:
            hashed_subpackets += p.to_bytes()
        body += pack('!H', len(hashed_subpackets)) + hashed_subpackets

        return body

    def body(self, trailer=False):
        if self.version == 2 or self.version == 3:
            body = pack('!B', self.version) + pack('!B', 5) + pack('!B', self.signature_type)

            for p in self.unhashed_subpackets:
                if isinstance(p, SignaturePacket.SignatureCreationTimePacket):
                    body += pack('!L', p.data)
                    break

            for p in self.unhashed_subpackets:
                if isinstance(p, SignaturePacket.IssuerPacket):
                    for i in range(0, len(p.data), 2):
                        body += pack('!B', int(p.data[i:i+2], 16))
                    break

            body += pack('!B', self.key_algorithm)
            body += pack('!B', self.hash_algorithm)
            body += pack('!H', self.hash_head)

            for mpi in self.data:
                body += pack('!H', bitlength(mpi)) + mpi

            return body
        else:
            if not self.trailer:
                self.trailer = self.calculate_trailer()
            body = self.trailer[0:-6]

            unhashed_subpackets = b''
            for p in self.unhashed_subpackets:
                unhashed_subpackets += p.to_bytes()
            body += pack('!H', len(unhashed_subpackets)) + unhashed_subpackets

            body += pack('!H', self.hash_head)
            for mpi in self.data:
                body += pack('!H', bitlength(mpi)) + mpi

            return body

    def key_algorithm_name(self):
        return PublicKeyPacket.algorithms[self.key_algorithm]

    def hash_algorithm_name(self):
        return self.hash_algorithms[self.hash_algorithm]

    def issuer(self):
        for p in self.hashed_subpackets:
            if isinstance(p, self.IssuerPacket):
                return p.data
        for p in self.unhashed_subpackets:
            if isinstance(p, self.IssuerPacket):
                return p.data
        return None

    @classmethod
    def get_subpackets(cls, input_data):
        subpackets = []
        length = len(input_data)
        while length > 0:
            subpacket, bytes_used = cls.get_subpacket(input_data)
            if bytes_used > 0:
                subpackets.append(subpacket)
                input_data = input_data[bytes_used:]
                length -= bytes_used
            else: # Parsing stuck?
                break
        return subpackets

    @classmethod
    def get_subpacket(cls, input_data):
        length = ord(input_data[0:1])
        length_of_length = 1
        # if($len < 192) One octet length, no furthur processing
        if length > 190 and length < 255: # Two octet length
            length_of_length = 2
            length = ((length - 192) << 8) + ord(input_data[1:2]) + 192
        if length == 255: # Five octet length
            length_of_length = 5
            length = unpack('!L', input_data[1:5])[0]
        input_data = input_data[length_of_length:] # Chop off length header
        tag = ord(input_data[0:1])

        try:
            klass = cls.subpacket_types[tag]
        except KeyError:
            klass = SignaturePacket.Subpacket

        packet = klass()
        packet.tag = tag
        packet.input = PushbackGenerator(_gen_one(input_data[1:length]))
        packet.length = length-1
        packet.read()
        packet.input = None
        packet.length = None

        input_data = input_data[length:] # Chop off the data from this packet
        return (packet, length_of_length + length)

    class Subpacket(Packet):
        def __init__(self, data=None):
             super(SignaturePacket.Subpacket, self).__init__()
             for tag in SignaturePacket.subpacket_types:
                 if SignaturePacket.subpacket_types[tag] == self.__class__:
                     self.tag = tag
                     break

        def header_and_body(self):
            body = self.body() or '' # Get body first, we'll need its length
            length = len(body) + 1
            # Use 1-, 2- or 5-octet lengths + 1 for type as first packet body octet
            if length <= 191:
                size = pack('!B', length)
            elif length <= (0x3FFF + 192):
                size = pack('!H', 0xC000 | (length - 192))
            else:
                size = pack('!B', 255) + pack('!L', length)
            tag = pack('!B', self.tag)
            return {'header': size + tag, 'body': body}

    class SignatureCreationTimePacket(Subpacket):
        """ http://tools.ietf.org/html/rfc4880#section-5.2.3.4 """
        def __init__(self, time=time()):
            super(SignaturePacket.SignatureCreationTimePacket, self).__init__()
            self.data = time

        def read(self):
            self.data = self.read_timestamp()

        def body(self):
            return pack('!L', int(self.data))

    class SignatureExpirationTimePacket(Subpacket):
        def read(self):
            self.data = self.read_timestamp()

        def body(self):
            return pack('!L', self.data)

    class ExportableCertificationPacket(Subpacket):
        def read(self):
            self.data = ord(self.read_byte()) != 0

        def body(self):
            return pack('!B', self.data and 1 or 0)

    class TrustSignaturePacket(Subpacket):
        def read(self):
            self.depth = ord(self.read_byte())
            self.trust = ord(self.read_byte())

        def body(self):
            return pack('!B', self.depth) + pack('!B', self.trust)

    class RegularExpressionPacket(Subpacket):
        def read(self):
            self.data = self.read_bytes(self.length-1)

        def body(self):
            return self.data + pack('!B', 0)

    class RevocablePacket(Subpacket):
        def read(self):
            self.data = ord(self.read_byte()) != 0

        def body(self):
            return pack('!B', self.data and 1 or 0)

    class KeyExpirationTimePacket(Subpacket):
        def read(self):
            self.data = self.read_timestamp()

        def body(self):
            return pack('!L', self.data)

    class PreferredSymmetricAlgorithmsPacket(Subpacket):
        def read(self):
            self.data = []
            while self.length > 0:
                self.data += [self.read_byte()]

        def body(self):
            body = b''
            for algo in self.data:
                body += pack('!B', algo)
            return body

    class RevocationKeyPacket(Subpacket):
        def read(self):
            # bitfield must have bit 0x80 set, says the spec
            bitfield = ord(self.read_byte())
            self.sensitive = bitfield & 0x40 == 0x40
            self.key_algorithm = ord(self.read_byte())

            self.fingerprint = ''
            while self.length > 0:
                self.fingerprint += '%02X' % ord(self.read_byte())

        def body(self):
            body = b''
            body += pack('!B', 0x80 | (self.sensitive and 0x40 or 0x00))
            body += pack('!B', self.key_algorithm)

            for i in range(0, len(self.data), 2):
                body += pack('!B', int(self.data[i] + self.data[i+1], 16))

            return body

    class IssuerPacket(Subpacket):
        """ http://tools.ietf.org/html/rfc4880#section-5.2.3.5 """
        def __init__(self, keyid=None):
            super(SignaturePacket.IssuerPacket, self).__init__()
            self.data = keyid

        def read(self):
            self.data = ''
            for i in range(0, 8): # Store KeyID in Hex
                self.data += '%02X' % ord(self.read_byte())

        def body(self):
            b = b''
            for i in range(0, len(self.data), 2):
                b += pack('!B', int(self.data[i] + self.data[i+1], 16))
            return b

    class NotationDataPacket(Subpacket):
        def read(self):
            flags = self.read_bytes(4)
            namelen = self.read_unpacked(2, '!H')
            datalen = self.read_unpacked(2, '!H')
            self.human_readable = ord(flags[0:1]) & 0x80 == 0x80
            self.name = self.read_bytes(namelen).decode('utf-8')
            self.data = self.read_bytes(datalen)
            if self.human_readable:
                self.data = self.data.decode('utf-8')

        def body(self):
            name_bytes = self.name.encode('utf-8')
            data_bytes = self.data
            if self.human_readable:
                data_bytes = data_bytes.encode('utf-8')
            return pack('!B', self.human_readable and 0x80 or 0x00) + b'\0\0\0' + \
                pack('!H', len(name_bytes)) + pack('!H', len(data_bytes)) + \
                name_bytes + data_bytes

    class PreferredHashAlgorithmsPacket(Subpacket):
        def read(self):
            self.data = []
            while self.length > 0:
                self.data += [self.read_byte()]

        def body(self):
            body = b''
            for algo in self.data:
                body += pack('!B', algo)
            return body

    class PreferredCompressionAlgorithmsPacket(Subpacket):
        def read(self):
            self.data = []
            while self.length > 0:
                self.data += [self.read_byte()]

        def body(self):
            body = b''
            for algo in self.data:
                body += pack('!B', algo)
            return body

    class KeyServerPreferencesPacket(Subpacket):
        def read(self):
            flags = ord(self.read_byte())
            self.no_modify = flags & 0x80 == 0x80

        def body(self):
            return pack('!B', self.no_modify and 0x80 or 0x00)

    class PreferredKeyServerPacket(Subpacket):
        def read(self):
            self.data = self.read_bytes(self.length)

        def body(self):
            return self.data

    class PrimaryUserIDPacket(Subpacket):
        def read(self):
            self.data = ord(self.read_byte()) != 0

        def body(self):
            return pack('!B', self.data and 1 or 0)

    class PolicyURIPacket(Subpacket):
        def read(self):
            self.data = self.read_bytes(self.length)

        def body(self):
            return self.data

    class KeyFlagsPacket(Subpacket):
        def __init__(self, flags=[]):
            super(SignaturePacket.KeyFlagsPacket, self).__init__()
            self.flags = flags

        def read(self):
          self.flags = []
          while self.length > 0:
              self.flags.append(ord(self.read_byte()))

        def body(self):
            b = b''
            for f in self.flags:
                b += pack('!B', f)
            return b

    class SignersUserIDPacket(Subpacket):
        def read(self):
            self.data = self.read_bytes(self.length)

        def body(self):
            return self.data

    class ReasonforRevocationPacket(Subpacket):
        def read(self):
            self.code = ord(self.read_byte())
            self.data = self.read_bytes(self.length)

        def body(self):
            return pack('!B', self.code) + self.data

    class FeaturesPacket(KeyFlagsPacket):
        pass # All implemented in parent

    class SignatureTargetPacket(Subpacket):
        def read(self):
            self.key_algorithm = ord(self.read_byte())
            self.hash_algorithm = ord(self.read_byte())
            self.data = self.read_bytes(self.length)

        def body(self):
            return pack('!B', self.key_algorithm) + pack('!B', self.hash_algorithm) + self.data

    hash_algorithms = {
        1: 'MD5',
        2: 'SHA1',
        3: 'RIPEMD160',
        8: 'SHA256',
        9: 'SHA384',
        10: 'SHA512',
        11: 'SHA224'
    }

    subpacket_types = {
        2: SignatureCreationTimePacket,
        3: SignatureExpirationTimePacket,
        4: ExportableCertificationPacket,
        5: TrustSignaturePacket,
        6: RegularExpressionPacket,
        7: RevocablePacket,
        9: KeyExpirationTimePacket,
        11: PreferredSymmetricAlgorithmsPacket,
        12: RevocationKeyPacket,
        16: IssuerPacket,
        20: NotationDataPacket,
        21: PreferredHashAlgorithmsPacket,
        22: PreferredCompressionAlgorithmsPacket,
        23: KeyServerPreferencesPacket,
        24: PreferredKeyServerPacket,
        25: PrimaryUserIDPacket,
        26: PolicyURIPacket,
        27: KeyFlagsPacket,
        28: SignersUserIDPacket,
        29: ReasonforRevocationPacket,
        30: FeaturesPacket,
        31: SignatureTargetPacket
    }

class EmbeddedSignaturePacket(SignaturePacket.Subpacket, SignaturePacket):
    pass

SignaturePacket.subpacket_types[32] = SignaturePacket.EmbeddedSignaturePacket = EmbeddedSignaturePacket

class SymmetricSessionKeyPacket(Packet):
    """ OpenPGP Symmetric-Key Encrypted Session Key packet (tag 3).
        http://tools.ietf.org/html/rfc4880#section-5.3
    """
    def __init__(self, s2k=None, encrypted_data=b'', symmetric_algorithm=9, version=3):
        super(SymmetricSessionKeyPacket, self).__init__()
        self.version = version
        self.symmetric_algorithm = symmetric_algorithm
        self.s2k = s2k
        self.encrypted_data = encrypted_data

    def read(self):
        self.version = ord(self.read_byte())
        self.symmetric_algorithm = ord(self.read_byte())
        self.s2k, s2k_bytes = S2K.parse(self.input)
        self.length -= s2k_bytes
        self.encrypted_data = self.read_bytes(self.length)

    def body(self):
        return pack('!B', self.version) + pack('!B', self.symmetric_algorithm) \
            + self.s2k.to_bytes() + self.encrypted_data

class OnePassSignaturePacket(Packet):
    """ OpenPGP One-Pass Signature packet (tag 4).
        http://tools.ietf.org/html/rfc4880#section-5.4
    """
    def read(self):
        self.version = ord(self.read_byte())
        self.signature_type = ord(self.read_byte())
        self.hash_algorithm = ord(self.read_byte())
        self.key_algorithm = ord(self.read_byte())
        self.key_id = ''
        for i in range(0, 8): # Store KeyID in Hex
          self.key_id += '%02X' % ord(self.read_byte())
        self.nested = ord(self.read_byte())

    def body(self):
        body = pack('!B', self.version) + pack('!B', self.signature_type) + pack('!B', self.hash_algorithm) + pack('!B', self.key_algorithm)
        for i in range(0, len(self.key_id), 2):
          body += pack('!B', int(self.key_id[i] + self.key_id[i+1], 16))
        body += pack('!B', int(self.nested))
        return body

class PublicKeyPacket(Packet):
    """ OpenPGP Public-Key packet (tag 6).
        http://tools.ietf.org/html/rfc4880#section-5.5.1.1
        http://tools.ietf.org/html/rfc4880#section-5.5.2
        http://tools.ietf.org/html/rfc4880#section-11.1
        http://tools.ietf.org/html/rfc4880#section-12
    """
    def __init__(self, keydata=None, version=4, algorithm=1, timestamp=time()):
        super(PublicKeyPacket, self).__init__()
        self._fingerprint = None
        self.version = version
        self.key_algorithm = algorithm
        self.timestamp = int(timestamp)
        if isinstance(keydata, tuple) or isinstance(keydata, list):
            self.key = {}
            for i in range(0, min(len(keydata), len(self.key_fields[self.key_algorithm]))):
                 self.key[self.key_fields[self.key_algorithm][i]] = keydata[i]
        else:
            self.key = keydata

    def self_signatures(self, message):
        """ Find self signatures in a message, these often contain metadata about the key """
        sigs = []
        keyid16 = self.fingerprint()[-16:].upper()
        for p in message:
            if isinstance(p, SignaturePacket):
                if(p.issuer() == keyid16):
                    sigs.append(p)
                else:
                    packets = p.hashed_subpackets + p.unhashed_subpackets
                    for s in packets:
                        if isinstance(s, SignaturePacket.EmbeddedSignaturePacket) and s.issuer().upper() == keyid16:
                            sigs.append(p)
                            break
            elif len(sigs) > 0:
                break # After we've seen a self sig, the next non-sig stop all self-sigs
        return sigs

    def expires(self, message):
        """ Find expiry time of this key based on the self signatures in a message """
        for p in self.self_signatures(message):
            packets = p.hashed_subpackets + p.unhashed_subpackets
            for s in packets:
                if isinstance(s, SignaturePacket.KeyExpirationTimePacket):
                    return self.timestamp + s.data
        return None # Never expires

    def key_algorithm_name(self):
        return self.__class__.algorithms[self.key_algorithm]

    def read(self):
        """ http://tools.ietf.org/html/rfc4880#section-5.5.2 """
        self.version = ord(self.read_byte())
        if self.version == 3:
            self.timestamp = self.read_timestamp()
            self.v3_days_of_validity = self.read_unpacked(2, '!H')
            self.key_algorithm = ord(self.read_byte())
            self.read_key_material()
        elif self.version == 4:
            self.timestamp = self.read_timestamp()
            self.key_algorithm = ord(self.read_byte())
            self.read_key_material()

    def read_key_material(self):
        self.key = {}
        for field in self.key_fields[self.key_algorithm]:
            self.key[field] = self.read_mpi()
        self.key_id = self.fingerprint()[-8:]

    def fingerprint_material(self):
        if self.version == 2 or self.version == 3:
            material = []
            for i in self.key_fields[self.key_algorithm]:
                material += [pack('!H', bitlength(self.key[i]))]
                material += [self.key[i]]
            return material
        elif self.version == 4:
            head = [pack('!B', 0x99), None, pack('!B', self.version), pack('!L', self.timestamp), pack('!B', self.key_algorithm)]
            material = b''
            for i in self.key_fields[self.key_algorithm]:
                material += pack('!H', bitlength(self.key[i]))
                material += self.key[i]
            head[1] = pack('!H', 6 + len(material))
            return head + [material]

    def fingerprint(self):
        """ http://tools.ietf.org/html/rfc4880#section-12.2
            http://tools.ietf.org/html/rfc4880#section-3.3
        """
        if self._fingerprint:
            return self._fingerprint
        if self.version == 2 or self.version == 3:
            self._fingerprint = hashlib.md5(b''.join(self.fingerprint_material())).hexdigest().upper()
        elif self.version == 4:
            self._fingerprint = hashlib.sha1(b''.join(self.fingerprint_material())).hexdigest().upper()
        return self._fingerprint

    def body(self):
        if self.version == 3:
            return b''.join([
                pack('!B', self.version), pack('!L', self.timestamp),
                pack('!H', self.v3_days_of_validity), pack('!B', self.key_algorithm)
            ] + self.fingerprint_material())
        elif self.version == 4:
            return b''.join(self.fingerprint_material()[2:])

    key_fields = {
        1: ['n', 'e'],          # RSA
       16: ['p', 'g', 'y'],     # ELG-E
       17: ['p', 'q', 'g', 'y'] # DSA
    }

    algorithms = {
        1: 'RSA',
        2: 'RSA',
        3: 'RSA',
       16: 'ELGAMAL',
       17: 'DSA',
       18: 'ECC',
       19: 'ECDSA',
       21: 'DH'
    }

class PublicSubkeyPacket(PublicKeyPacket):
    """ OpenPGP Public-Subkey packet (tag 14).
        http://tools.ietf.org/html/rfc4880#section-5.5.1.2
        http://tools.ietf.org/html/rfc4880#section-5.5.2
        http://tools.ietf.org/html/rfc4880#section-11.1
        http://tools.ietf.org/html/rfc4880#section-12
    """
    pass

class SecretKeyPacket(PublicKeyPacket):
    """ OpenPGP Secret-Key packet (tag 5).
        http://tools.ietf.org/html/rfc4880#section-5.5.1.3
        http://tools.ietf.org/html/rfc4880#section-5.5.3
        http://tools.ietf.org/html/rfc4880#section-11.2
        http://tools.ietf.org/html/rfc4880#section-12
    """
    def __init__(self, keydata=None, version=4, algorithm=1, timestamp=time()):
        super(SecretKeyPacket, self).__init__(keydata, version, algorithm, timestamp)
        self.s2k_useage = 0
        if isinstance(keydata, tuple) or isinstance(keydata, list):
            public_len = len(self.key_fields[self.key_algorithm])
            for i in range(public_len, len(keydata)):
                 self.key[self.secret_key_fields[self.key_algorithm][i-public_len]] = keydata[i]

    def read(self):
        super(SecretKeyPacket, self).read() # All the fields from PublicKey
        self.s2k_useage = ord(self.read_byte())
        if self.s2k_useage == 255 or self.s2k_useage == 254:
            self.symmetric_algorithm = ord(self.read_byte())
            self.s2k, s2k_bytes = S2K.parse(self.input)
            self.length -= s2k_bytes
        elif self.s2k_useage > 0:
            self.symmetric_algorithm = self.s2k_useage
        if self.s2k_useage > 0:
            # Rest of input is MPIs and checksum (encrypted)
            self.encrypted_data = self.read_bytes(self.length)
        else:
            material = self.read_bytes(self.length - 2)
            self.input.push(material)
            self.key_from_input()
            chk = self.read_unpacked(2, '!H')
            if chk != checksum(material):
                raise OpenPGPException("Checksum verification failed when parsing SecretKeyPacket")

    def key_from_input(self):
        for field in self.secret_key_fields[self.key_algorithm]:
            self.key[field] = self.read_mpi()

    def body(self):
        b = super(SecretKeyPacket, self).body() + pack('!B', self.s2k_useage)
        secret_material = b''
        if self.s2k_useage == 255 or self.s2k_useage == 254:
            b += pack('!B', self.symmetric_algorithm)
            b += self.s2k.to_bytes()
        if self.s2k_useage > 0:
            b += self.encrypted_data
        else:
            for f in self.secret_key_fields[self.key_algorithm]:
                f = self.key[f]
                secret_material += pack('!H', bitlength(f))
                secret_material += f
            b += secret_material

            # 2-octet checksum
            chk = 0
            for i in range(0, len(secret_material)):
                chk = (chk + ord(secret_material[i:i+1])) % 65536
            b += pack('!H', chk)

        return b

    secret_key_fields = {
        1: ['d', 'p', 'q', 'u'], # RSA
       16: ['x'],                # ELG-E
       17: ['x'],                # DSA
    }

class SecretSubkeyPacket(SecretKeyPacket):
    """ OpenPGP Secret-Subkey packet (tag 7).
        http://tools.ietf.org/html/rfc4880#section-5.5.1.4
        http://tools.ietf.org/html/rfc4880#section-5.5.3
        http://tools.ietf.org/html/rfc4880#section-11.2
        http://tools.ietf.org/html/rfc4880#section-12
    """
    pass

class CompressedDataPacket(Packet):
    """ OpenPGP Compressed Data packet (tag 8).
        http://tools.ietf.org/html/rfc4880#section-5.6
    """
    # http://tools.ietf.org/html/rfc4880#section-9.3
    algorithms = {0: 'Uncompressed', 1: 'ZIP', 2: 'ZLIB', 3: 'BZip2'}

    def read(self):
        self.algorithm = ord(self.read_byte())
        self.data = self.read_bytes(self.length)
        if self.algorithm == 0:
            self.data = Message.parse(self.data)
        elif self.algorithm == 1:
            self.data = Message.parse(zlib.decompress(self.data, -15))
        elif self.algorithm == 2:
            self.data = Message.parse(zlib.decompress(self.data))
        elif self.algorithm == 3:
            self.data = Message.parse(bz2.decompress(self.data))
        else:
            pass # TODO: error?

    def body(self):
        body = pack('!B', self.algorithm)
        if self.algorithm == 0:
            self.data = self.data.to_bytes()
        elif self.algorithm == 1:
            compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
            body += compressor.compress(self.data.to_bytes())
            body += compressor.flush()
        elif self.algorithm == 2:
            body += zlib.compress(self.data.to_bytes())
        elif self.algorithm == 3:
            body += bz2.compress(self.data.to_bytes())
        else:
            pass # TODO: error?
        return body

    def __iter__(self):
        return iter(self.data)

    def __getitem__(self, item):
        return self.data[item]

class EncryptedDataPacket(Packet):
    """ OpenPGP Symmetrically Encrypted Data packet (tag 9).
        http://tools.ietf.org/html/rfc4880#section-5.7
    """
    pass # Everything inherited

class MarkerPacket(Packet):
    """ OpenPGP Marker packet (tag 10).
        http://tools.ietf.org/html/rfc4880#section-5.8
    """
    pass # TODO

class LiteralDataPacket(Packet):
    """ OpenPGP Literal Data packet (tag 11).
        http://tools.ietf.org/html/rfc4880#section-5.9
    """
    def __init__(self, data=None, format='b', filename='data', timestamp=time()):
        super(LiteralDataPacket, self).__init__()
        if hasattr(data, 'encode'):
            data = data.encode('utf-8')
        self.data = data
        self.format = format
        self.filename = filename.encode('utf-8')
        self.timestamp = timestamp

    def normalize(self):
        if self.format == 'u' or self.format == 't': # Normalize line endings
            self.data = self.data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")

    def read(self):
        self.size = self.length - 1 - 1 - 4
        self.format = self.read_byte().decode('ascii')
        filename_length = ord(self.read_byte())
        self.size -= filename_length
        self.filename = self.read_bytes(filename_length)
        self.timestamp = self.read_timestamp()
        self.data = self.read_bytes(self.size)

    def body(self):
        return self.format.encode('ascii') + pack('!B', len(self.filename)) + self.filename + pack('!L', int(self.timestamp)) + self.data

class TrustPacket(Packet):
    """ OpenPGP Trust packet (tag 12).
        http://tools.ietf.org/html/rfc4880#section-5.10
    """
    pass # Data is implementation-specific

class UserIDPacket(Packet):
    """ OpenPGP User ID packet (tag 13).
        http://tools.ietf.org/html/rfc4880#section-5.11
        http://tools.ietf.org/html/rfc2822
    """
    def __init__(self, name='', comment=None, email=None):
        super(UserIDPacket, self).__init__()
        self.name = self.comment = self.email = None
        self.text = ''
        if (not comment) and (not email):
            name = name.encode('utf-8')
            self.input = PushbackGenerator(_gen_one(name))
            self.length = len(name)
            self.read()
        else:
            self.name = name
            self.comment = comment
            self.email = email

    def read(self):
        self.text = self.read_bytes(self.length).decode('utf-8')
        # User IDs of the form: "name (comment) <email>"
        parts = re.findall('^([^\(]+)\(([^\)]+)\)\s+<([^>]+)>$', self.text)
        if len(parts) > 0:
            self.name = parts[0][0].strip()
            self.comment = parts[0][1].strip()
            self.email = parts[0][2].strip()
        else: # User IDs of the form: "name <email>"
            parts = re.findall('^([^<]+)\s+<([^>]+)>$', self.text)
            if len(parts) > 0:
                self.name = parts[0][0].strip()
                self.email = parts[0][1].strip()
            else: # User IDs of the form: "name"
                parts = re.findall('^([^<]+)$', self.text)
                if len(parts) > 0:
                    self.name = parts[0][1].strip()
                else: # User IDs of the form: "<email>"
                    parts = re.findall('^<([^>]+)>$', self.text)
                    if len(parts) > 0:
                        self.email = parts[0][1].strip()

    def __str__(self):
        text = []
        if self.name:
            text.append(self.name)
        if self.comment:
            text.append('('+self.comment+')')
        if self.email:
            text.append('<'+self.email+'>')
        if len(text) < 1:
            text = [self.text]
        return ' '.join(text)

    def body(self):
        return self.__str__().encode('utf-8')

class UserAttributePacket(Packet):
    """ OpenPGP User Attribute packet (tag 17).
        http://tools.ietf.org/html/rfc4880#section-5.12
        http://tools.ietf.org/html/rfc4880#section-11.1
    """
    pass # TODO

class IntegrityProtectedDataPacket(EncryptedDataPacket):
    """ OpenPGP Sym. Encrypted Integrity Protected Data packet (tag 18).
        http://tools.ietf.org/html/rfc4880#section-5.13
    """
    def __init__(self, data=b'', version=1):
        super(IntegrityProtectedDataPacket, self).__init__()
        self.version = version
        self.data = data

    def read(self):
        self.version = ord(self.read_byte())
        self.data = self.read_bytes(self.length)

    def body(self):
        return pack('!B', self.version) + self.data

class ModificationDetectionCodePacket(Packet):
    """ OpenPGP Modification Detection Code packet (tag 19).
        http://tools.ietf.org/html/rfc4880#section-5.14
    """
    def __init__(self, sha1=''):
        super(ModificationDetectionCodePacket, self).__init__()
        self.data = sha1

    def read(self):
        self.data = self.read_bytes(self.length)
        if(len(self.data) != 20):
            raise Exception("Bad ModificationDetectionCodePacket")

    def header_and_body(self):
        body = self.body() # Get body first, we will need it's length
        if(len(body) != 20):
            raise Exception("Bad ModificationDetectionCodePacket")
        return {'header': b'\xD3\x14', 'body': body }

    def body(self):
        return self.data

class ExperimentalPacket(Packet):
    """ OpenPGP Private or Experimental packet (tags 60..63).
        http://tools.ietf.org/html/rfc4880#section-4.3
    """
    pass

Packet.tags = {
     1: AsymmetricSessionKeyPacket, # Public-Key Encrypted Session Key
     2: SignaturePacket, # Signature Packet
     3: SymmetricSessionKeyPacket, # Symmetric-Key Encrypted Session Key Packet
     4: OnePassSignaturePacket, # One-Pass Signature Packet
     5: SecretKeyPacket, # Secret-Key Packet
     6: PublicKeyPacket, # Public-Key Packet
     7: SecretSubkeyPacket, # Secret-Subkey Packet
     8: CompressedDataPacket, # Compressed Data Packet
     9: EncryptedDataPacket, # Symmetrically Encrypted Data Packet
    10: MarkerPacket, # Marker Packet
    11: LiteralDataPacket, # Literal Data Packet
    12: TrustPacket, # Trust Packet
    13: UserIDPacket, # User ID Packet
    14: PublicSubkeyPacket, # Public-Subkey Packet
    17: UserAttributePacket, # User Attribute Packet
    18: IntegrityProtectedDataPacket, # Sym. Encrypted and Integrity Protected Data Packet
    19: ModificationDetectionCodePacket, # Modification Detection Code Packet
    60: ExperimentalPacket, # Private or Experimental Values
    61: ExperimentalPacket, # Private or Experimental Values
    62: ExperimentalPacket, # Private or Experimental Values
    63: ExperimentalPacket, # Private or Experimental Values
}
