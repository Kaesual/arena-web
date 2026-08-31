// SPDX-License-Identifier: GPL-2.0-or-later
// Native dedicated-server harness for the real Sys_SendPacket WP7 boundary.

#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

#include "../ioq3/code/qcommon/q_shared.h"
#include "../ioq3/code/qcommon/qcommon.h"

static int refusalLogs;

ssize_t sendto( int socket, const void *buffer, size_t length, int flags,
	const struct sockaddr *address, socklen_t addressLength )
{
	(void)socket;
	(void)buffer;
	(void)flags;
	(void)address;
	(void)addressLength;
	return (ssize_t)length;
}

void QDECL Com_Printf( const char *format, ... )
{
	if ( strstr( format, "arena_net refusal" ) != NULL )
		refusalLogs++;
}

void QDECL Com_Error( int level, const char *format, ... )
{
	(void)level;
	(void)format;
	abort();
}

int main( void )
{
	byte payload[769] = { 0 };
	netadr_t destination;

	memset( &destination, 0, sizeof(destination) );
	destination.type = NA_IP;
	destination.ip[0] = 127;
	destination.ip[3] = 1;
	destination.port = BigShort( 27960 );

	Sys_SendPacket( NS_SERVER, NET_PACKET_ORIGINATED, 768, payload,
		destination );
	assert( refusalLogs == 0 );

	Sys_SendPacket( NS_SERVER, NET_PACKET_ORIGINATED, 769, payload,
		destination );
	assert( refusalLogs == 1 );

	memset( payload, 0xff, 4 );
	memcpy( payload + 4, "echo", 4 );
	Sys_SendPacket( NS_SERVER, NET_PACKET_ELICITED, 769, payload,
		destination );
	assert( refusalLogs == 2 );

	puts( "wp7 real Sys_SendPacket boundary harness: passed" );
	return 0;
}
