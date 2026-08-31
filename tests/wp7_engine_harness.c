// SPDX-License-Identifier: GPL-2.0-or-later
// Native unit harness for the WP7 datagram boundary. The sender and receiver
// below are the pinned engine's real Netchan_Transmit/Netchan_Process code.

#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../ioq3/code/qcommon/q_shared.h"
#include "../ioq3/code/qcommon/qcommon.h"

#define ARENA_INNER_FLOOR 768
#define ARENA_FRAGMENT_SIZE 704
#define MAX_PACKETLEN 1400
#define MAX_CAPTURED_PACKETS 32

typedef struct {
	int length;
	byte data[MAX_PACKETLEN];
	netsrc_t sock;
	netpacketclass_t packetClass;
} capturedPacket_t;

static capturedPacket_t captured[MAX_CAPTURED_PACKETS];
static int capturedCount;
static int milliseconds;
static cvar_t disabledCvar;
static cvar_t qportCvar = { .integer = 40000 };
static cvar_t unitTimescale = { .value = 1.0f, .integer = 1 };

cvar_t *cl_packetdelay = &disabledCvar;
cvar_t *sv_packetdelay = &disabledCvar;
cvar_t *com_timescale = &unitTimescale;

extern cvar_t *showpackets;
extern cvar_t *showdrop;
extern cvar_t *net_qport;

void QDECL Com_Printf( const char *format, ... )
{
	(void)format;
}

void QDECL Com_Error( int level, const char *format, ... )
{
	(void)level;
	(void)format;
	abort();
}

int Sys_Milliseconds( void )
{
	return ++milliseconds;
}

const char *NET_AdrToString( netadr_t address )
{
	(void)address;
	return "test-address";
}

void *S_MallocDebug( int size, char *label, char *file, int line )
{
	(void)label;
	(void)file;
	(void)line;
	return calloc( 1, (size_t)size );
}

void Z_Free( void *pointer )
{
	free( pointer );
}

void Sys_SendPacket( netsrc_t sock, netpacketclass_t packetClass,
	int length, const void *data, netadr_t to )
{
	(void)to;
	assert( capturedCount < MAX_CAPTURED_PACKETS );
	assert( length <= (int)sizeof(captured[capturedCount].data) );
	captured[capturedCount].length = length;
	captured[capturedCount].sock = sock;
	captured[capturedCount].packetClass = packetClass;
	memcpy( captured[capturedCount].data, data, (size_t)length );
	capturedCount++;
}

static int expected_packets( int length )
{
	if ( length < ARENA_FRAGMENT_SIZE )
		return 1;
	return length / ARENA_FRAGMENT_SIZE + 1;
}

static void exercise_direction( netsrc_t senderSock, int length )
{
	byte source[MAX_MSGLEN];
	byte messageBuffer[MAX_MSGLEN + 4];
	msg_t message;
	netchan_t sender;
	netchan_t receiver;
	netadr_t address;
	int index;
	qboolean complete = qfalse;

	assert( length >= 0 && length <= MAX_MSGLEN );
	for ( index = 0; index < length; index++ )
		source[index] = (byte)((index * 31 + length) & 0xff);

	memset( &address, 0, sizeof(address) );
	address.type = NA_IP6;
	address.port = BigShort( 40000 );
	capturedCount = 0;
	Netchan_Setup( senderSock, &sender, address, 40000, 0x12345678,
		qfalse );
	Netchan_Setup( senderSock == NS_CLIENT ? NS_SERVER : NS_CLIENT,
		&receiver, address, 40000, 0x12345678, qfalse );

	Netchan_Transmit( &sender, length, source );
	while ( sender.unsentFragments )
		Netchan_TransmitNextFragment( &sender );

	if ( capturedCount != expected_packets( length ) )
		fprintf( stderr, "packet count mismatch sock=%d length=%d got=%d expected=%d\n",
			senderSock, length, capturedCount, expected_packets( length ) );
	assert( capturedCount == expected_packets( length ) );
	for ( index = 0; index < capturedCount; index++ ) {
		assert( captured[index].packetClass == NET_PACKET_ORIGINATED );
		assert( captured[index].length <= ARENA_INNER_FLOOR );
		MSG_Init( &message, messageBuffer, sizeof(messageBuffer) );
		memcpy( message.data, captured[index].data,
			(size_t)captured[index].length );
		message.cursize = captured[index].length;
		complete = Netchan_Process( &receiver, &message );
		if ( index + 1 < capturedCount )
			assert( complete == qfalse );
	}
	assert( complete == qtrue );
	if ( length >= ARENA_FRAGMENT_SIZE ) {
		assert( message.cursize == length + 4 );
		assert( memcmp( message.data + 4, source, (size_t)length ) == 0 );
	} else {
		assert( message.cursize - message.readcount == length );
		assert( memcmp( message.data + message.readcount, source,
			(size_t)length ) == 0 );
	}

	if ( length == 703 )
		assert( captured[0].length == 703 +
			(senderSock == NS_CLIENT ? 10 : 8) );
	if ( length == 704 ) {
		assert( captured[0].length == 704 +
			(senderSock == NS_CLIENT ? 14 : 12) );
		assert( captured[1].length ==
			(senderSock == NS_CLIENT ? 14 : 12) );
	}
}

static void test_real_netchan_round_trips( void )
{
	static const int lengths[] = { 0, 703, 704, 705, 1408, 2304, MAX_MSGLEN };
	int index;

	showpackets = &disabledCvar;
	showdrop = &disabledCvar;
	net_qport = &qportCvar;
	for ( index = 0; index < (int)ARRAY_LEN(lengths); index++ ) {
		exercise_direction( NS_SERVER, lengths[index] );
		exercise_direction( NS_CLIENT, lengths[index] );
	}
}

static void test_compressed_connect( void )
{
	byte connectData[512];
	netadr_t destination;
	int index;

	memset( &destination, 0, sizeof(destination) );
	destination.type = NA_IP;
	for ( index = 0; index < (int)sizeof(connectData); index++ )
		connectData[index] = (byte)('!' + (index % 90));

	capturedCount = 0;
	cl_packetdelay->integer = 1;
	NET_OutOfBandData( NS_CLIENT, destination, connectData,
		(int)sizeof(connectData) );
	assert( capturedCount == 0 );
	NET_FlushPacketQueue();
	assert( capturedCount == 0 );
	NET_FlushPacketQueue();
	assert( capturedCount == 1 );
	assert( captured[0].packetClass == NET_PACKET_ORIGINATED );
	assert( captured[0].length != (int)sizeof(connectData) + 4 );
	assert( captured[0].length <= ARENA_INNER_FLOOR );
	assert( captured[0].data[0] == 0xff );
	cl_packetdelay->integer = 0;
}

int main( void )
{
	test_real_netchan_round_trips();
	test_compressed_connect();
	puts( "wp7 real engine boundary harness: passed" );
	return 0;
}
