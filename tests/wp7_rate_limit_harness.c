// SPDX-License-Identifier: GPL-2.0-or-later
// Behavioral harness for the real server-side relay rate-limit bucket lookup.

#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../ioq3/code/server/sv_main.c"

static int now = 1000;

int Sys_Milliseconds( void )
{
	return now++;
}

static netadr_t address( unsigned short port )
{
	netadr_t value;

	memset( &value, 0, sizeof(value) );
	value.type = NA_IP;
	value.ip[0] = 192;
	value.ip[1] = 0;
	value.ip[2] = 2;
	value.ip[3] = 1;
	value.port = port;
	return value;
}

static void resetBuckets( void )
{
	memset( buckets, 0, sizeof(buckets) );
	memset( bucketHashes, 0, sizeof(bucketHashes) );
}

int main( void )
{
	cvar_t setting;
	leakyBucket_t *first;
	leakyBucket_t *second;

	memset( &setting, 0, sizeof(setting) );
	sv_rateLimitPerPort = &setting;

	resetBuckets();
	setting.integer = 0;
	first = SVC_BucketForAddress( address( 10000 ), 10, 1000 );
	second = SVC_BucketForAddress( address( 20000 ), 10, 1000 );
	assert( first != NULL );
	assert( first == second );
	assert( first->port == 0 );

	resetBuckets();
	setting.integer = 1;
	first = SVC_BucketForAddress( address( 10000 ), 10, 1000 );
	second = SVC_BucketForAddress( address( 20000 ), 10, 1000 );
	assert( first != NULL );
	assert( second != NULL );
	assert( first != second );
	assert( first->port == 10000 );
	assert( second->port == 20000 );
	assert( SVC_BucketForAddress( address( 10000 ), 10, 1000 ) == first );

	puts( "wp7 real rate-limit bucket harness: passed" );
	return 0;
}
